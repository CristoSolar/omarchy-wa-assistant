# sink/store.py
"""State on disk: the queue, the per-chat context ring, and the config.

Every write goes through atomic_write so a crash can never leave a
half-written JSON file behind.
"""
import json
import os
import tempfile
from pathlib import Path

CONTEXT_LIMIT = 10

# The layout is split on purpose:
#
#   <state root>/            0700, host only
#     secrets.env            0600, WA_TOKEN et al — NEVER mounted anywhere
#     state/                 queue.json, ctx/, config.json — mounted into
#                            the sink, which is the internet-facing side
#     wuzdata/               wuzapi's SQLite — mounted into both
#
# The sink imports urllib.parse and never urllib.request, so it cannot send
# anything today; but handing the internet-facing container both the
# credential and the network route would make that boundary behavioural
# rather than structural. It only ever sees `state/` and `wuzdata/`.
DEFAULT_STATE_ROOT = Path.home() / ".local/share/omarchy-wa"


def state_root() -> Path:
    return Path(os.environ.get("WA_STATE_ROOT", DEFAULT_STATE_ROOT))


def data_dir() -> Path:
    """The sink-visible state directory: queue.json, ctx/, config.json."""
    d = Path(os.environ.get("WA_DATA_DIR", state_root() / "state"))
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    (d / "ctx").mkdir(exist_ok=True)
    return d


def secrets_path() -> Path:
    """Where WA_TOKEN lives.

    Resolved from its own environment variable and its own default, never
    derived from WA_DATA_DIR: the credential and the sink's mount must be
    able to move independently, and deriving one from the other is how the
    secret ended up inside the container's bind mount in the first place.
    """
    return Path(os.environ.get("WA_SECRETS_FILE", state_root() / "secrets.env"))


def atomic_write(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path, default):
    # A missing or corrupt file is not fatal: the queue is best-effort and
    # ZapZap still holds the real messages.
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def load_config(d) -> dict:
    return read_json(Path(d) / "config.json", {"groups_enabled": []})


def load_queue(d) -> list:
    q = read_json(Path(d) / "queue.json", [])
    return q if isinstance(q, list) else []


def _save_queue(d, queue) -> list:
    atomic_write(Path(d) / "queue.json", json.dumps(queue, ensure_ascii=False))
    return queue


def upsert_queue(d, entry) -> list:
    queue = load_queue(d)
    for existing in queue:
        if existing["id"] == entry["id"]:
            existing["count"] = existing.get("count", 1) + 1
            existing["text"] = entry["text"]
            existing["ts"] = entry["ts"]
            existing["push_name"] = entry.get("push_name") or existing.get("push_name")
            existing["status"] = "pending"
            return _save_queue(d, queue)
    queue.append(dict(entry))
    return _save_queue(d, queue)


def set_status(d, entry_id, status) -> list:
    queue = load_queue(d)
    for e in queue:
        if e["id"] == entry_id:
            e["status"] = status
    return _save_queue(d, queue)


def remove_queue(d, entry_id) -> list:
    queue = [e for e in load_queue(d) if e["id"] != entry_id]
    return _save_queue(d, queue)


def _ctx_path(d, jid) -> Path:
    safe = jid.replace("/", "_")
    return Path(d) / "ctx" / f"{safe}.json"


def append_context(d, jid, entry, limit=CONTEXT_LIMIT) -> list:
    p = _ctx_path(d, jid)
    ring = read_json(p, [])
    if not isinstance(ring, list):
        ring = []
    ring.append(entry)
    ring = ring[-limit:]
    atomic_write(p, json.dumps(ring, ensure_ascii=False))
    return ring


def load_context(d, jid) -> list:
    ring = read_json(_ctx_path(d, jid), [])
    return ring if isinstance(ring, list) else []
