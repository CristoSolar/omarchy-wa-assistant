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


# Types a row sent through wuzapi's API is stored as, with no datajson: the
# file went out, but nothing of it is kept to fetch it back.
SENT_MEDIA = ("image", "video", "document", "audio", "sticker")


class SchemaError(Exception):
    """message_history is not the table this module was written against."""


def _file_name(message) -> str:
    """The sender's own file name, reduced to a plain basename: it comes
    off the wire, so no separators, control chars or leading dots."""
    doc = message.get("documentMessage") if isinstance(message, dict) else None
    raw = doc.get("fileName") if isinstance(doc, dict) else None
    if not isinstance(raw, str):
        return ""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip().lstrip(".")
    return name[:200]


def _context(message) -> dict | None:
    """The contextInfo (mentions, quoted message) of whichever kind of
    message this is: text, caption of a photo, a sticker..."""
    for inner in (message.values() if isinstance(message, dict) else ()):
        if isinstance(inner, dict) and isinstance(inner.get("contextInfo"), dict):
            return inner["contextInfo"]
    return None


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
    ctx = _context(message)
    msg = {
        "id": rid, "message_id": mid,
        "from_me": sender == "me" or bool(info.get("IsFromMe")),
        "sender": info.get("Sender") or sender,
        "push_name": info.get("PushName") or "",
        "ts": info.get("Timestamp") or _go_time(ts),
        "text": _text_of(message) if message else (text or ""),
        "mentions": list((ctx or {}).get("mentionedJID") or []),
    }
    quoted = (ctx or {}).get("quotedMessage")
    if isinstance(quoted, dict):
        msg["quote"] = {"message_id": str(ctx.get("stanzaID") or ""), "sender": str(ctx.get("participant") or ""),
                        "text": _text_of(_unwrap(quoted))}
    attachment = _attachment_of(message)
    if attachment:
        atype = attachment["type"]
        video = message.get("videoMessage")
        if atype == "video" and isinstance(video, dict) and video.get("gifPlayback"):
            atype = "gif"  # wa-media still fetches it as the video it is
        msg["attachment"] = {"type": atype, "downloadable": attachment["downloadable"]}
        name = _file_name(message)
        if name:
            msg["attachment"]["name"] = name
    elif not message and mtype in SENT_MEDIA:
        msg["attachment"] = {"type": mtype, "downloadable": False}
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
    page = _decoded(reversed(rows))
    if page:
        found = reactions(conn, uid, jid)
        for m in page:
            if m["message_id"] in found:
                m["reactions"] = found[m["message_id"]]
        quoted = [m["quote"]["message_id"] for m in page if m.get("quote")]
        if quoted:
            marks = ",".join("?" * len(quoted))
            mine = {r[0] for r in conn.execute(
                f"SELECT message_id FROM message_history WHERE user_id = ? AND message_id IN ({marks})"
                f" AND NOT ({_INCOMING})", (uid, *quoted))}
            for m in page:
                if m.get("quote"):
                    m["quote"]["from_me"] = m["quote"]["message_id"] in mine
    return page, len(rows) == limit


def reactions(conn, uid, jid) -> dict:
    """target message_id -> its emojis, one per reactor, in arrival order.
    A reactor's newer reaction replaces the older; an empty one takes it back."""
    # ponytail: scans the chat's every reaction per page; index by target if a chat gets huge.
    rows = conn.execute("SELECT sender_jid, datajson FROM message_history WHERE user_id = ? AND chat_jid = ?"
                        " AND message_type = 'reaction' ORDER BY id", (uid, jid)).fetchall()
    by_target = {}
    for sender, datajson in rows:
        try:
            event = json.loads(datajson) if datajson else {}
        except ValueError:
            continue
        info = event.get("Info") if isinstance(event.get("Info"), dict) else {}
        r = (_unwrap(event.get("Message") or {}) or {}).get("reactionMessage")
        target = ((r or {}).get("key") or {}).get("ID") if isinstance(r, dict) else None
        if not target:
            continue
        who = "me" if sender == "me" or info.get("IsFromMe") else (info.get("Sender") or sender)
        reactors = by_target.setdefault(target, {})
        reactors.pop(who, None)
        if r.get("text"):
            reactors[who] = r["text"]
    return {t: list(v.values()) for t, v in by_target.items() if v}


def recent_stickers(conn, uid, limit=24) -> list:
    """message_ids of the account's newest stickers, one per distinct file."""
    rows = conn.execute("SELECT message_id, json_extract(datajson, '$.Message.stickerMessage.fileSHA256')"
                        " FROM message_history WHERE user_id = ? AND message_type = 'sticker'"
                        " AND json_valid(datajson) ORDER BY id DESC", (uid,)).fetchall()
    seen, out = set(), []
    for mid, sha in rows:
        key = sha or mid  # a wrapped (ephemeral) sticker has no sha at that path
        if key not in seen:
            seen.add(key)
            out.append(mid)
            if len(out) == limit:
                break
    return out


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
    elif jid is None:
        row = conn.execute("SELECT MAX(id) FROM message_history WHERE user_id = ?", (uid,)).fetchone()
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
