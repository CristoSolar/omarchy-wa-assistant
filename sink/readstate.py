"""state/read.json: how far the owner has read each chat.

A marker is a wuzapi `message_history.id` (the autoincrement row id, see
sink/history.py), never a time. `since` is the highest id when the file was
first created, so history that predates this feature never shows as unread.
`pending` maps ACC:JID to the newest row id the owner clicked while wuzapi
could not take the read receipt; bin/wa-chat retries those, never past it.
The sink and bin/wa-chat both write it (each via `ensure`/`mark`, atomic replace).
"""
import json
from pathlib import Path

from sink import store


def _path(d) -> Path:
    return Path(d) / "read.json"


def _key(account, jid) -> str:
    return store.entry_id(account, jid)


def load(d) -> dict:
    raw = store.read_json(_path(d), {})
    raw = raw if isinstance(raw, dict) else {}
    since, chats = raw.get("since", 0), raw.get("chats", {})
    pending = raw.get("pending", {})
    if isinstance(pending, list):  # the first form had no top: the marker was it
        pending = {k: chats.get(k, since) for k in pending}
    return {"since": since, "chats": chats, "pending": pending}


def _save(d, state) -> None:
    store.atomic_write(_path(d), json.dumps(state))


def ensure(d, current_max) -> dict:
    if not _path(d).exists():
        _save(d, {"since": current_max, "chats": {}, "pending": {}})
    return load(d)


def marker(state, account, jid) -> int:
    return state["chats"].get(_key(account, jid), state["since"])


def mark(d, account, jid, row_id, pending=None) -> None:
    state = load(d)
    key = _key(account, jid)
    state["chats"][key] = max(state["chats"].get(key, 0), row_id)
    if pending is True:
        state["pending"][key] = max(state["pending"].get(key, 0), row_id)
    elif pending is False:
        state["pending"].pop(key, None)
    _save(d, state)
