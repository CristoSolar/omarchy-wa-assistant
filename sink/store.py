# sink/store.py
"""State on disk: the queue, the per-chat context ring, and the config.

Every write goes through atomic_write so a crash can never leave a
half-written JSON file behind.
"""
import json
import os
import sys
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


def _ctx_path(d, jid, account) -> Path:
    """ctx/<account>/<jid>.json.

    `account` is required, not defaulted: a call site that forgets it must
    fail loudly (TypeError) rather than silently reading/writing the
    pre-migration flat ctx/<jid>.json — a post-migration install has no
    reader for that path any more, so a quiet fallback there would look
    like an empty context ring and start a second, invisible one.
    """
    safe = jid.replace("/", "_")
    return Path(d) / "ctx" / account / f"{safe}.json"


def append_context(d, jid, entry, account, limit=CONTEXT_LIMIT) -> list:
    p = _ctx_path(d, jid, account)
    ring = read_json(p, [])
    if not isinstance(ring, list):
        ring = []
    ring.append(entry)
    ring = ring[-limit:]
    atomic_write(p, json.dumps(ring, ensure_ascii=False))
    return ring


def load_context(d, jid, account) -> list:
    ring = read_json(_ctx_path(d, jid, account), [])
    return ring if isinstance(ring, list) else []


# --- Multi-account: composite queue ids -------------------------------


def entry_id(account_id: str, jid: str) -> str:
    """The queue entry id for a (account, jid) pair: `<account_id>:<jid>`.

    Composite on purpose: with the id equal to the bare jid, the same
    contact writing to two accounts would produce two events with the same
    id and one entry would overwrite the other in the queue.
    """
    return f"{account_id}:{jid}"


def split_entry_id(eid: str):
    """The inverse of entry_id: `<account_id>:<jid>` -> (account_id, jid).

    Splits on the FIRST colon only. An account id is a slug and cannot
    contain one; a JID can and real ones do (e.g.
    "237786636488820:23@lid"). Splitting on the first colon is the only
    split that can never cut a JID in the wrong place — rsplit or a
    split-all would corrupt such a JID and resolve the entry to the wrong
    chat.
    """
    account_id, sep, jid = eid.partition(":")
    if not sep:
        raise ValueError(f"not a composite entry id (no ':'): {eid!r}")
    return account_id, jid


# --- Migration: single account -> accounts.json ------------------------


def migrate_single_account(state_root) -> bool:
    """Migrate a pre-multi-account install to the accounts.json layout.

    Follows the spec's five steps. Idempotent: a second run is a no-op,
    and a run against an already-migrated install (accounts.json already
    exists) changes nothing and returns False immediately — that check is
    the ONLY gate, so every step below must tolerate being re-applied to
    state a previous, interrupted run already partly migrated.
    accounts.json is written last, once every other step has succeeded,
    so it doubles as the commit marker: if the process dies before it is
    written, the next run has no accounts.json, redoes the (idempotent)
    steps, and reaches the same end state either way. If it dies after
    accounts.json is written but before the final config.json cleanup, the
    only residue is a harmless duplicate `quick_replies` key left behind in
    config.json — never data loss.

    The sink-visible directory and the secrets file are resolved the same
    way data_dir()/secrets_path() resolve them — an explicit WA_DATA_DIR or
    WA_SECRETS_FILE override wins over a location derived from
    `state_root` — so an install that has moved either independently of
    its state root is migrated in the exact place its own processes read
    from, never a state_root-derived path nothing else looks at.
    """
    state_root = Path(state_root)
    data_dir = Path(os.environ.get("WA_DATA_DIR", state_root / "state"))
    data_dir.mkdir(parents=True, exist_ok=True)
    accounts_path = data_dir / "accounts.json"
    if accounts_path.exists():
        return False

    account_id = "personal"

    # Step 1 (data collection only; the write happens last): read the
    # existing config so we know what quick_replies to carry over.
    config = load_config(data_dir)
    quick_replies = config.get("quick_replies", [])
    if not isinstance(quick_replies, list):
        quick_replies = []

    # Step 3: rewrite each queue entry, idempotently. Only entries that do
    # not already carry an "account" field are pre-migration; a partial
    # prior run may have already rewritten some.
    queue = load_queue(data_dir)
    changed_queue = False
    for e in queue:
        if "account" not in e:
            e["account"] = account_id
            e["id"] = entry_id(account_id, e["id"])
            changed_queue = True
    if changed_queue:
        _save_queue(data_dir, queue)

    # Step 4: move the context ring rather than abandon it. Only files
    # still sitting flat under ctx/ need moving; a partial prior run may
    # have already moved some. If a retry finds BOTH a flat file and an
    # already-moved one for the same jid — e.g. the first run moved it,
    # then crashed before writing accounts.json, and the still-legacy
    # server.py wrote fresh messages to a new flat file in the meantime —
    # the flat one is never dropped: the two rings are merged (oldest
    # first, newest last, capped at CONTEXT_LIMIT) so nothing that arrived
    # during the interruption is lost.
    ctx_dir = data_dir / "ctx"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    account_ctx_dir = ctx_dir / account_id
    account_ctx_dir.mkdir(exist_ok=True)
    for p in ctx_dir.glob("*.json"):
        target = account_ctx_dir / p.name
        if not target.exists():
            os.replace(p, target)
            continue
        source_ring = read_json(p, [])
        if not isinstance(source_ring, list):
            source_ring = []
        target_ring = read_json(target, [])
        if not isinstance(target_ring, list):
            target_ring = []
        merged = (target_ring + source_ring)[-CONTEXT_LIMIT:]
        atomic_write(target, json.dumps(merged, ensure_ascii=False))
        p.unlink()
        print(f"migrate_single_account: merged context found at both "
              f"{p} and {target} into the latter (retry after an "
              f"interrupted run) — no messages were dropped",
              file=sys.stderr, flush=True)

    # Step 2: copy WA_TOKEN to WA_TOKEN_PERSONAL in secrets.env, keeping
    # the original so an in-progress session is never broken by this.
    secrets_file = Path(os.environ.get("WA_SECRETS_FILE", state_root / "secrets.env"))
    try:
        lines = secrets_file.read_text().splitlines()
    except FileNotFoundError:
        lines = []
    token_value = None
    has_personal = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("WA_TOKEN=") and "=" in stripped:
            token_value = stripped.split("=", 1)[1]
        if stripped.startswith("WA_TOKEN_PERSONAL="):
            has_personal = True
    if token_value is not None and not has_personal:
        new_text = "\n".join(lines)
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f"WA_TOKEN_PERSONAL={token_value}\n"
        atomic_write(secrets_file, new_text)
        os.chmod(secrets_file, 0o600)

    # Steps 1 + 5, combined into the single atomic write that commits the
    # migration: create accounts.json with the personal account, carrying
    # over quick_replies.
    account = {
        "id": account_id,
        "label": "Personal",
        "wuzapi_user": "omarchy",
        "agent": "respuesta.md",
        "quick_replies": quick_replies,
    }
    atomic_write(accounts_path, json.dumps([account], ensure_ascii=False))

    # Cleanup: drop quick_replies from config.json now that it lives on
    # the account, leaving groups_enabled where it is. Best-effort — if
    # this never runs (crash right after the write above), the only
    # residue is a harmless duplicate, not a loss.
    if "quick_replies" in config:
        config = dict(config)
        del config["quick_replies"]
        atomic_write(data_dir / "config.json", json.dumps(config, ensure_ascii=False))

    return True
