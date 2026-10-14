# tests/test_history.py
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import history

DM = "56911111111@s.whatsapp.net"
GROUP = "120363000000000001@g.us"
GO_TS = "2026-10-13 22:05:40.309828882 -0300 -03 m=+137327.354645224"

# wuzapi's own DDL (copied from a live users.db), so a schema change on
# their side shows up here as a diff, not as a silent mismatch.
DDL = """CREATE TABLE message_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    chat_jid TEXT NOT NULL, sender_jid TEXT NOT NULL, message_id TEXT NOT NULL,
    timestamp DATETIME NOT NULL, message_type TEXT NOT NULL, text_content TEXT,
    media_link TEXT, quoted_message_id TEXT, datajson TEXT,
    UNIQUE(user_id, message_id))"""


def _event(chat, text=None, from_me=False, sender=None, push="Ana", ts="2026-10-13T22:05:39-03:00",
           message=None, mid="M1"):
    msg = message if message is not None else {"conversation": text}
    return json.dumps({"Info": {"Chat": chat, "Sender": sender or chat, "IsFromMe": from_me,
                                "IsGroup": chat.endswith("@g.us"), "PushName": push,
                                "Timestamp": ts, "ID": mid}, "Message": msg})


class Db:
    def __init__(self, d):
        self.path = d / "users.db"
        self.c = sqlite3.connect(self.path)
        self.c.execute("CREATE TABLE users (id TEXT, name TEXT, jid TEXT)")
        self.c.execute("INSERT INTO users VALUES ('u1', 'omarchy', '56900000001:27@s.whatsapp.net')")
        self.c.execute("INSERT INTO users VALUES ('u2', 'trabajo', '56900000002:3@s.whatsapp.net')")
        self.c.execute(DDL)
        self.n = 0

    def add(self, chat, text="hola", uid="u1", sender=None, mtype="text", datajson=None, **kw):
        self.n += 1
        mid = f"M{self.n}"
        if datajson is None:
            datajson = _event(chat, text, sender=sender, mid=mid, **kw)
        self.c.execute("INSERT INTO message_history (user_id, chat_jid, sender_jid, message_id, timestamp,"
                       " message_type, text_content, media_link, quoted_message_id, datajson)"
                       " VALUES (?,?,?,?,?,?,?,'','',?)",
                       (uid, chat, sender or chat, mid, GO_TS, mtype, text, datajson))
        self.c.commit()
        return self.c.execute("SELECT max(id) FROM message_history").fetchone()[0]


def _conn(d):
    return history.connect(d)


def test_a_live_message_reads_its_text_time_and_sender_from_the_event():
    with tmpdir() as d:
        db = Db(d); db.add(DM, "hola", push="Ana")
        msgs, more = history.messages(_conn(d), "u1", DM)
        assert [m["text"] for m in msgs] == ["hola"] and not more
        assert msgs[0]["from_me"] is False and msgs[0]["push_name"] == "Ana"
        assert msgs[0]["ts"] == "2026-10-13T22:05:39-03:00"


def test_a_message_sent_through_the_api_has_no_event_and_is_still_mine():
    with tmpdir() as d:
        db = Db(d); db.add(DM, "te escribo", sender="me", datajson="")
        (m,), _ = history.messages(_conn(d), "u1", DM)
        assert m["from_me"] is True and m["text"] == "te escribo"
        assert m["ts"] == "2026-10-13T22:05:40-03:00"


def test_reactions_and_protocol_messages_never_show():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "", mtype="reaction", message={"reactionMessage": {"text": "👍"}})
        db.add(DM, "", message={"protocolMessage": {"type": 0}})
        db.add(DM, "real")
        msgs, _ = history.messages(_conn(d), "u1", DM)
        assert [m["text"] for m in msgs] == ["real"]


def test_chats_are_newest_first_and_never_mix_accounts():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "viejo"); db.add(GROUP, "nuevo"); db.add(DM, "otra cuenta", uid="u2")
        chats = history.chats(_conn(d), "u1")
        assert [c["jid"] for c in chats] == [GROUP, DM]
        assert chats[1]["last"]["text"] == "viejo"


def test_messages_page_backwards_from_before():
    with tmpdir() as d:
        db = Db(d)
        ids = [db.add(DM, f"m{i}") for i in range(5)]
        page, more = history.messages(_conn(d), "u1", DM, limit=2)
        assert [m["text"] for m in page] == ["m3", "m4"] and more
        page, more = history.messages(_conn(d), "u1", DM, before=page[0]["id"], limit=2)
        assert [m["text"] for m in page] == ["m1", "m2"] and more
        page, more = history.messages(_conn(d), "u1", DM, before=ids[1], limit=2)
        assert [m["text"] for m in page] == ["m0"] and not more


def test_unread_counts_only_incoming_after_the_marker():
    with tmpdir() as d:
        db = Db(d)
        first = db.add(DM, "leído")
        db.add(DM, "nuevo 1"); db.add(DM, "mío", from_me=True); db.add(DM, "nuevo 2")
        assert history.unread_count(_conn(d), "u1", DM, first) == 2
        ids = history.unread_ids(_conn(d), "u1", DM, first)
        assert [s for _, s in ids] == [DM, DM]


def test_an_image_carries_its_attachment_type():
    with tmpdir() as d:
        db = Db(d); db.add(DM, "", mtype="image", message={"imageMessage": {"URL": "u"}})
        (m,), _ = history.messages(_conn(d), "u1", DM)
        assert m["attachment"] == {"type": "image", "downloadable": False}
        assert m["text"] == "[imagen]"


def test_a_mention_is_listed():
    with tmpdir() as d:
        db = Db(d)
        db.add(GROUP, "", sender="237786636488820:23@lid", message={"extendedTextMessage": {
            "text": "@yo mira", "contextInfo": {"mentionedJID": ["56900000001@s.whatsapp.net"]}}})
        (m,), _ = history.messages(_conn(d), "u1", GROUP)
        assert m["mentions"] == ["56900000001@s.whatsapp.net"]


def test_rows_after_returns_every_account_in_order():
    with tmpdir() as d:
        db = Db(d)
        a = db.add(DM, "a"); db.add(DM, "b", uid="u2"); db.add(GROUP, "c")
        rows = history.rows_after(_conn(d), a)
        assert [(u, j, m["text"]) for u, j, m in rows] == [("u2", DM, "b"), ("u1", GROUP, "c")]
        assert history.last_id(_conn(d)) == a + 2
        assert history.last_id(_conn(d), "u1", DM) == a


def test_message_event_and_has_chat():
    with tmpdir() as d:
        db = Db(d); db.add(DM, "hola")
        assert history.message_event(_conn(d), "u1", "M1")["Info"]["ID"] == "M1"
        assert history.message_event(_conn(d), "u2", "M1") is None
        assert history.has_chat(_conn(d), "u1", DM) and not history.has_chat(_conn(d), "u2", DM)
        assert history.user_ids(_conn(d)) == {"omarchy": "u1", "trabajo": "u2"}


def test_an_unknown_schema_is_refused_not_guessed():
    with tmpdir() as d:
        c = sqlite3.connect(d / "users.db")
        c.execute("CREATE TABLE message_history (id INTEGER, user_id TEXT)"); c.commit()
        try:
            history.connect(d)
        except history.SchemaError:
            return
        raise AssertionError("expected SchemaError")


if __name__ == "__main__":
    run(dict(globals()))
