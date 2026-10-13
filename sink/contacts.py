"""Who a chat is, in words the owner recognises.

WhatsApp now addresses most chats by an opaque `@lid`, and events without
a PushName left the panel showing `237786636488820@lid`. whatsmeow already
keeps the answer in its own store (wuzdata/main.db): the owner's address
book synced from the phone, the names people chose, and the lid -> phone
number map. This reads it, read-only, and never needs a wuzapi token —
the sink still holds no credentials.

Lookup order: the owner's address-book name, then a business name, then
the name the sender chose (from the event, else as stored), then the phone
number, then the raw jid. Any failure to read the store falls back to what
the event said, so a name lookup can never stop a message being queued.
"""
import sqlite3
from pathlib import Path


def _ro(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


def _bare(jid: str) -> str:
    """'569…:27@s.whatsapp.net' (a device) -> '569…@s.whatsapp.net'."""
    user, _, server = (jid or "").partition("@")
    return user.split(":")[0] + "@" + server if server else ""


def _our_jid(wuzdata: Path, instance_name: str) -> str:
    conn = _ro(wuzdata / "users.db")
    try:
        row = conn.execute("SELECT jid FROM users WHERE name = ?", (instance_name,)).fetchone()
    finally:
        conn.close()
    return _bare(row[0]) if row and row[0] else ""


def display_name(wuzdata, instance_name: str, chat_jid: str, push_name: str) -> str:
    event_name = push_name if push_name and push_name != chat_jid else ""
    try:
        wuzdata = Path(wuzdata)
        our = _our_jid(wuzdata, instance_name)
        if not our:
            return event_name or chat_jid
        conn = _ro(wuzdata / "main.db")
        try:
            pn = chat_jid if chat_jid.endswith("@s.whatsapp.net") else ""
            if chat_jid.endswith("@lid"):
                row = conn.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid = ?",
                                   (chat_jid.split("@")[0],)).fetchone()
                pn = row[0] + "@s.whatsapp.net" if row and row[0] else ""

            def contact(jid):
                if not jid:
                    return ("", "", "")
                row = conn.execute(
                    "SELECT full_name, business_name, push_name FROM whatsmeow_contacts"
                    " WHERE our_jid = ? AND their_jid = ?", (our, jid)).fetchone()
                return tuple(v or "" for v in row) if row else ("", "", "")

            by_pn, by_chat = contact(pn), contact(chat_jid if chat_jid != pn else "")
        finally:
            conn.close()
    except sqlite3.Error:
        return event_name or chat_jid

    for name in (by_pn[0], by_chat[0], by_pn[1], by_chat[1], event_name, by_pn[2], by_chat[2]):
        if name:
            return name
    if pn:
        return "+" + pn.split("@")[0]
    return chat_jid
