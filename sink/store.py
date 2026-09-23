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


class StoreReadError(Exception):
    """A state file exists but could not be read as JSON.

    Deliberately distinct from "the file is missing": a missing file is a
    legitimate empty state (first run, nothing queued yet) and must stay
    silent. This is the opposite case — the file is there and something is
    wrong with it (permissions, corruption) — and the caller must not treat
    it as "no state". This is the failure this project shipped once
    already: `wa-reply list` printed a confident `[]`, `wa-status` said
    `pending: 0`, and nothing told the user their queue was unreadable.
    Every read_json caller either lets this propagate somewhere the user
    will see it, or turns it into an explicit loud signal — none of them
    may swallow it back into a default.
    """

    def __init__(self, path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"cannot read {self.path}: {reason}")


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
    # A MISSING file is not fatal and must stay silent: the queue is
    # best-effort, ZapZap still holds the real messages, and a first run
    # with no state at all yet is a legitimate empty state.
    #
    # A file that EXISTS but cannot be read (permissions, most notably a
    # root-owned state file the invoking user cannot open) or that exists
    # and is not valid JSON (corruption) is a broken install, not an empty
    # one — raise StoreReadError so it can never be mistaken for the
    # former.
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise StoreReadError(path, str(exc)) from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise StoreReadError(path, f"invalid JSON: {exc}") from exc


def load_config(d) -> dict:
    return read_json(Path(d) / "config.json", {"groups_enabled": []})


def load_quick_replies(d) -> list:
    """The one-click quick-reply texts, in configured order.

    A missing key or a missing config.json both read as an empty list —
    the panel then simply shows no quick-reply buttons — but a
    config.json that exists and cannot be read/parsed still raises
    StoreReadError via load_config(), same as every other read here:
    never mistake a broken install for "no quick replies configured".
    """
    ql = load_config(d).get("quick_replies", [])
    return ql if isinstance(ql, list) else []


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
            # A new inbound message does not resolve an unconfirmed send:
            # the reply we may already have delivered is still unconfirmed,
            # and clearing the status here would silently unlock the resend
            # guard from the outside. "sending" counts too — an entry left
            # there was interrupted mid-POST, which is the same ambiguity.
            # Everything else goes back to pending.
            if existing.get("status") not in ("unknown", "sending"):
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
