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


# whatsmeow keys its tables by the device jid ('569…:27@…'); users.db has it bare.
_OWNER = "(our_jid = ? OR our_jid LIKE ?)"


def _owner(our: str) -> tuple:
    return (our, our.split("@")[0] + ":%")


def our_ids(wuzdata, instance_name: str) -> set:
    """The account's own number jid and its @lid, without a device part.

    A group mention names the owner by either one, so both count.
    """
    try:
        wuzdata = Path(wuzdata)
        our = _our_jid(wuzdata, instance_name)
        if not our:
            return set()
        conn = _ro(wuzdata / "main.db")
        try:
            row = conn.execute("SELECT lid FROM whatsmeow_lid_map WHERE pn = ?",
                               (our.split("@")[0],)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return set()
    return {our} | ({row[0] + "@lid"} if row and row[0] else set())


def muted_chats(wuzdata, instance_name: str, now: float) -> set:
    """Chats the owner muted on the phone (-1 = forever, else Unix seconds).

    Unreadable store -> empty set: a muted chat notifying beats a lost message.
    """
    try:
        wuzdata = Path(wuzdata)
        our = _our_jid(wuzdata, instance_name)
        if not our:
            return set()
        conn = _ro(wuzdata / "main.db")
        try:
            rows = conn.execute(
                f"SELECT chat_jid FROM whatsmeow_chat_settings WHERE {_OWNER}"
                " AND (muted_until = -1 OR muted_until > ?)", (*_owner(our), int(now))).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return set()
    return {r[0] for r in rows}


def address_book(wuzdata, instance_name: str) -> list:
    """[{name, phone}] the owner saved on the phone, by name. Unreadable -> []."""
    try:
        wuzdata = Path(wuzdata)
        our = _our_jid(wuzdata, instance_name)
        if not our:
            return []
        conn = _ro(wuzdata / "main.db")
        try:
            rows = conn.execute(
                f"SELECT full_name, their_jid FROM whatsmeow_contacts WHERE {_OWNER}"
                " AND full_name <> '' AND their_jid LIKE '_%@s.whatsapp.net'", _owner(our)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out = [{"name": n, "phone": j.split("@")[0]} for n, j in rows]
    return sorted(out, key=lambda c: c["name"].casefold())


def with_mention_names(text: str, mentions, name_of) -> str:
    """'@105189… hola' -> '@Ana hola' for each mentioned jid (name_of(jid))."""
    for jid in mentions or ():
        user = str(jid).partition("@")[0].split(":")[0]
        if user and "@" + user in text:
            text = text.replace("@" + user, "@" + name_of(jid))
    return text


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
                    f" WHERE {_OWNER} AND their_jid = ?", (*_owner(our), jid)).fetchone()
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
