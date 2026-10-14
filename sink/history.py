"""The chats wuzapi keeps in users.db, read-only.

With `history` set on a wuzapi user, every message that account sees —
incoming, sent from the phone, sent through the API — lands in
`message_history`, and `datajson` holds the very event the webhook carries.
This module is the only place that knows that schema: bin/wa-chat,
bin/wa-reply, bin/wa-media, bin/wa-notify and the sink all go through it,
so a wuzapi upgrade that changes the table breaks one file, loudly
(SchemaError), instead of five quietly.

Row ids, not times, order everything. The id is wuzapi's autoincrement, so
it is arrival order across both accounts; the `timestamp` column is the
insert time in Go's String() format and the real time sits in
`datajson.Info.Timestamp`.
"""
import json
import sqlite3
from pathlib import Path

from sink.events import _attachment_of, _is_invisible, _text_of, _unwrap

COLUMNS = {"id", "user_id", "chat_jid", "sender_jid", "message_id", "timestamp",
           "message_type", "text_content", "datajson"}
# ponytail: edits and deletes are hidden, not applied to the message they
# change; apply them when someone misses it.
HIDDEN_TYPES = ("reaction", "delete", "edit")
_SELECT = "SELECT id, sender_jid, message_id, timestamp, message_type, text_content, datajson FROM message_history"
_VISIBLE = f"message_type NOT IN {HIDDEN_TYPES}"
# The only servers that are conversations. Everything else (status@broadcast,
# broadcast lists, @newsletter channels) is left out of every listing, and
# bin/wa-reply and bin/wa-media refuse it: whatsmeow sends a message to
# status@broadcast as a public Status post.
CHAT_SERVERS = ("s.whatsapp.net", "lid", "g.us")
_IS_CHAT = "(" + " OR ".join(f"chat_jid LIKE '_%@{s}'" for s in CHAT_SERVERS) + ")"
_INCOMING = ("sender_jid != 'me' AND CASE WHEN json_valid(datajson)"
             " THEN json_extract(datajson, '$.Info.IsFromMe') END = 0")


class SchemaError(Exception):
    """message_history is not the table this module was written against."""


def is_chat(jid) -> bool:
    user, at, server = str(jid or "").rpartition("@")
    return bool(user and at) and server in CHAT_SERVERS


def connect(wuzdata) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(wuzdata) / 'users.db'}?mode=ro", uri=True, timeout=2)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(message_history)")}
    if not COLUMNS <= cols:
        conn.close()
        raise SchemaError(f"message_history lacks {sorted(COLUMNS - cols)}")
    return conn


def user_ids(conn) -> dict:
    """wuzapi user name -> the id it stamps on every history row."""
    return dict(conn.execute("SELECT name, id FROM users"))


def _go_time(value) -> str:
    """'2026-10-13 22:05:40.3 -0300 -03 m=+1' -> '2026-10-13T22:05:40-03:00'."""
    parts = str(value or "").split()
    if len(parts) < 3 or len(parts[2]) != 5:
        return ""
    return f"{parts[0]}T{parts[1][:8]}{parts[2][:3]}:{parts[2][3:]}"


def _decode(row) -> dict | None:
    rid, sender, mid, ts, mtype, text, datajson = row
    if mtype in HIDDEN_TYPES:
        return None
    try:
        event = json.loads(datajson) if datajson else {}
    except ValueError:
        event = {}
    info = event.get("Info") if isinstance(event.get("Info"), dict) else {}
    message = _unwrap(event.get("Message") or {})
    if _is_invisible(message):
        return None
    ext = message.get("extendedTextMessage") if isinstance(message, dict) else None
    ctx = ext.get("contextInfo") if isinstance(ext, dict) else None
    msg = {
        "id": rid, "message_id": mid,
        "from_me": sender == "me" or bool(info.get("IsFromMe")),
        "sender": info.get("Sender") or sender,
        "push_name": info.get("PushName") or "",
        "ts": info.get("Timestamp") or _go_time(ts),
        "text": _text_of(message) if message else (text or ""),
        "mentions": list((ctx or {}).get("mentionedJID") or []),
    }
    attachment = _attachment_of(message)
    if attachment:
        msg["attachment"] = {"type": attachment["type"], "downloadable": attachment["downloadable"]}
    return msg


def _decoded(rows) -> list:
    return [m for m in map(_decode, rows) if m is not None]


def chats(conn, uid) -> list:
    last = conn.execute(f"SELECT chat_jid, MAX(id) FROM message_history WHERE user_id = ? AND {_VISIBLE}"
                        f" AND {_IS_CHAT} GROUP BY chat_jid ORDER BY MAX(id) DESC", (uid,)).fetchall()
    out = []
    for jid, last_id in last:
        msg = _decode(conn.execute(f"{_SELECT} WHERE id = ?", (last_id,)).fetchone())
        if msg is None:  # an invisible message (key exchange) was the last one
            page, _ = messages(conn, uid, jid, limit=20)
            msg = page[-1] if page else None
        if msg is not None:
            out.append({"jid": jid, "last_id": last_id, "last": msg})
    return out


def messages(conn, uid, jid, before=None, limit=50):
    """One page, oldest first, plus whether older rows exist."""
    rows = conn.execute(f"{_SELECT} WHERE user_id = ? AND chat_jid = ? AND id < ? ORDER BY id DESC LIMIT ?",
                        (uid, jid, before if before is not None else 2**62, limit)).fetchall()
    # ponytail: `limit` counts hidden rows too, so a page can come back short;
    # `more` still says whether to ask for the next one.
    return _decoded(reversed(rows)), len(rows) == limit


def _unread(conn, uid, jid, after_id) -> list:
    """Incoming rows after the marker that the chat shows: same rule as _decode."""
    rows = conn.execute(f"{_SELECT} WHERE user_id = ? AND chat_jid = ? AND id > ? AND {_INCOMING} ORDER BY id",
                        (uid, jid, after_id)).fetchall()
    return [r for r in rows if _decode(r) is not None]


def unread_count(conn, uid, jid, after_id) -> int:
    return len(_unread(conn, uid, jid, after_id))


def unread_ids(conn, uid, jid, after_id) -> list:
    """(message_id, sender) of every unread visible message, for /chat/markread.
    Hidden rows are left out on purpose: the badge, the chat and the read
    receipts all describe the same set."""
    return [(r[2], r[1]) for r in _unread(conn, uid, jid, after_id)]


def read_ids_upto(conn, uid, jid, top, limit) -> list:
    """(message_id, sender) of the newest `limit` incoming visible rows at or
    below `top`: what a read the owner clicked covered, for a retry. Rows
    that arrived after the click are never in it."""
    rows = conn.execute(f"{_SELECT} WHERE user_id = ? AND chat_jid = ? AND id <= ? AND {_INCOMING}"
                        " ORDER BY id DESC LIMIT ?", (uid, jid, top, limit)).fetchall()
    return [(r[2], r[1]) for r in reversed(rows) if _decode(r) is not None]


def last_id(conn, uid=None, jid=None) -> int:
    if uid is None:
        row = conn.execute("SELECT MAX(id) FROM message_history").fetchone()
    else:
        row = conn.execute("SELECT MAX(id) FROM message_history WHERE user_id = ? AND chat_jid = ?",
                           (uid, jid)).fetchone()
    return row[0] or 0


def rows_after(conn, after_id) -> list:
    """(user_id, chat_jid, msg) for every visible row newer than after_id."""
    rows = conn.execute("SELECT user_id, chat_jid, id, sender_jid, message_id, timestamp, message_type,"
                        f" text_content, datajson FROM message_history WHERE id > ? AND {_IS_CHAT} ORDER BY id",
                        (after_id,)).fetchall()
    return [(r[0], r[1], m) for r in rows if (m := _decode(r[2:])) is not None]


def message_event(conn, uid, message_id) -> dict | None:
    """The stored webhook event of one message (what wa-media needs to fetch it)."""
    row = conn.execute("SELECT datajson FROM message_history WHERE user_id = ? AND message_id = ?",
                       (uid, message_id)).fetchone()
    try:
        return json.loads(row[0]) if row and row[0] else None
    except ValueError:
        return None


def chat_of(conn, uid, message_id) -> str | None:
    row = conn.execute("SELECT chat_jid FROM message_history WHERE user_id = ? AND message_id = ?",
                       (uid, message_id)).fetchone()
    return row[0] if row else None


def has_chat(conn, uid, jid) -> bool:
    return conn.execute("SELECT 1 FROM message_history WHERE user_id = ? AND chat_jid = ? LIMIT 1",
                        (uid, jid)).fetchone() is not None
