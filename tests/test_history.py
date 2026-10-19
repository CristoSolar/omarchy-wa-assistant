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


def test_unread_ignores_what_the_chat_hides():
    with tmpdir() as d:
        db = Db(d)
        first = db.add(DM, "leído")
        db.add(DM, "", message={"protocolMessage": {"type": 0}})
        db.add(DM, "", mtype="reaction", message={"reactionMessage": {"text": "x"}})
        real = db.add(DM, "nuevo")
        c = _conn(d)
        shown = [m["id"] for m in history.messages(c, "u1", DM)[0] if m["id"] > first]
        assert shown == [real]
        assert history.unread_count(c, "u1", DM, first) == 1
        # marking read covers exactly what is counted: hidden rows are not marked
        assert len(history.unread_ids(c, "u1", DM, first)) == 1


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


STATUS = "status@broadcast"
LID = "237786636488820@lid"


def test_only_person_lid_and_group_chats_are_chats():
    assert history.is_chat(DM) and history.is_chat(GROUP) and history.is_chat(LID)
    for jid in (STATUS, "123@broadcast", "1203630@newsletter", "", "x", "@s.whatsapp.net", None):
        assert not history.is_chat(jid), jid


def test_status_broadcast_and_newsletters_never_list_nor_notify():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "a"); db.add(LID, "b")
        db.add(STATUS, "mi estado", sender="56933@s.whatsapp.net")
        db.add("1203630@newsletter", "canal")
        db.add("123@broadcast", "lista")
        assert [c["jid"] for c in history.chats(_conn(d), "u1")] == [LID, DM]
        assert [j for _, j, _ in history.rows_after(_conn(d), 0)] == [DM, LID]
        assert history.chat_of(_conn(d), "u1", "M3") == STATUS
        assert history.chat_of(_conn(d), "u1", "nope") is None


def test_reactions_attach_to_their_message_newest_per_person_and_can_be_taken_back():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "foto")  # M1
        react = lambda who, emoji: db.add(DM, "", sender=who, mtype="reaction",
                                          message={"reactionMessage": {"key": {"ID": "M1"}, "text": emoji}})
        react("a@s.whatsapp.net", "👍"); react("b@s.whatsapp.net", "😮")
        react("a@s.whatsapp.net", "❤️"); react("b@s.whatsapp.net", "")
        (m,), _ = history.messages(_conn(d), "u1", DM)
        assert m["reactions"] == ["❤️"]


def test_a_gif_is_its_own_type_and_recent_stickers_skip_repeats():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "", mtype="video", message={"videoMessage": {"gifPlayback": True}})
        sticker = lambda sha: db.add(DM, "", mtype="sticker", message={"stickerMessage": {"fileSHA256": sha}})
        sticker("A"); sticker("B"); sticker("A")
        (m, *_), _ = history.messages(_conn(d), "u1", DM)
        assert m["attachment"]["type"] == "gif"
        assert history.recent_stickers(_conn(d), "u1") == ["M4", "M3"]


def test_a_document_keeps_the_senders_file_name_as_a_plain_basename():
    doc = lambda n: {"documentMessage": {"fileName": n}}
    assert history._file_name(doc("Salfa_Soporte.xlsx")) == "Salfa_Soporte.xlsx"
    assert history._file_name(doc("../../.bashrc")) == "bashrc"
    assert history._file_name(doc("C:\\x\\a\nb.pdf")) == "ab.pdf"
    assert history._file_name({"imageMessage": {}}) == ""


def test_a_reply_carries_the_quoted_message_and_whether_it_was_mine():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "", sender="me", datajson="")  # M1: sent through the API, no datajson
        ctx = lambda mid: {"stanzaID": mid, "participant": DM, "quotedMessage": {"conversation": "la original"}}
        db.add(DM, "sí", message={"extendedTextMessage": {"text": "sí", "contextInfo": ctx("M1")}})
        db.add(DM, "", message={"imageMessage": {"caption": "", "contextInfo": ctx("NOPE")}}, mtype="image")
        msgs, _ = history.messages(_conn(d), "u1", DM)
        assert msgs[1]["quote"] == {"message_id": "M1", "sender": DM, "text": "la original", "from_me": True}
        assert msgs[2]["quote"]["from_me"] is False
        assert "quote" not in msgs[0]


def test_a_file_sent_through_the_api_still_shows_as_an_attachment():
    with tmpdir() as d:
        db = Db(d)
        db.add(DM, "la guía", sender="me", mtype="document", datajson="")
        db.add(DM, "", sender="me", mtype="image", datajson="")
        db.add(DM, "texto", sender="me", datajson="")
        a, b, c = history.messages(_conn(d), "u1", DM)[0]
        assert a["attachment"] == {"type": "document", "downloadable": False} and a["text"] == "la guía"
        assert b["attachment"]["type"] == "image" and "attachment" not in c


if __name__ == "__main__":
    run(dict(globals()))
