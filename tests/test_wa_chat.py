# tests/test_wa_chat.py
import importlib.machinery, importlib.util, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from tests.test_history import Db, DM, GROUP
from sink import readstate

_p = Path(__file__).resolve().parents[1] / "bin" / "wa-chat"
_loader = importlib.machinery.SourceFileLoader("wa_chat", str(_p))
_spec = importlib.util.spec_from_loader("wa_chat", _loader)
wc = importlib.util.module_from_spec(_spec)
_loader.exec_module(wc)

ACCOUNTS = [{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"},
            {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo"}]


def _setup(d, groups_enabled=()):
    (d / "accounts.json").write_text(json.dumps(ACCOUNTS))
    (d / "config.json").write_text(json.dumps({"groups_enabled": list(groups_enabled)}))
    return Db(d)


def _get(names=None):
    return lambda acc, path: {"data": {"Groups": [{"JID": j, "Name": n} for j, n in (names or {}).items()]}}


class Post:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, acc, path, body):
        if self.fail:
            raise OSError("down")
        self.calls.append((acc, path, body))
        return {}


def _chats(d, post=None, get=None):
    return wc.list_chats(d, d, post or Post(), get=get or _get())["chats"]


def test_chats_list_names_groups_muted_and_unread():
    with tmpdir() as d:
        db = _setup(d, groups_enabled=[])
        readstate.ensure(d, 0)
        db.add(DM, "hola", push="Ana"); db.add(GROUP, "buenas", sender="56922@s.whatsapp.net")
        cs = _chats(d, get=_get({GROUP: "Equipo"}))
        by = {c["jid"]: c for c in cs}
        assert by[GROUP]["name"] == "Equipo" and by[GROUP]["group"] and not by[GROUP]["muted"]
        assert by[DM]["name"] == "Ana" and not by[DM]["group"] and not by[DM]["muted"]
        assert by[DM]["unread"] == 1 and by[DM]["account"] == "personal" and by[DM]["label"] == "Personal"
        assert by[DM]["last_text"] == "hola" and by[DM]["last_id"] == 1


def _main_db(d, muted=(), contacts=()):
    """whatsmeow's store, keyed by the device jid of u1 (personal)."""
    m = sqlite3.connect(d / "main.db")
    m.execute("CREATE TABLE whatsmeow_chat_settings (our_jid TEXT, chat_jid TEXT, muted_until BIGINT)")
    m.execute("CREATE TABLE whatsmeow_contacts (our_jid TEXT, their_jid TEXT, first_name TEXT,"
              " full_name TEXT, push_name TEXT, business_name TEXT, redacted_phone TEXT)")
    m.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT)")
    dev = "56900000001:27@s.whatsapp.net"
    m.executemany("INSERT INTO whatsmeow_chat_settings VALUES (?, ?, -1)", [(dev, j) for j in muted])
    m.executemany("INSERT INTO whatsmeow_contacts VALUES (?, ?, '', ?, '', '', '')",
                  [(dev, j, n) for j, n in contacts])
    m.commit(); m.close()


def test_a_chat_muted_on_the_phone_is_muted_unless_enabled():
    with tmpdir() as d:
        db = _setup(d)
        _main_db(d, muted=[GROUP])
        db.add(GROUP, "buenas", sender="56922@s.whatsapp.net"); db.add(DM, "hola")
        assert {c["jid"]: c["muted"] for c in _chats(d)} == {GROUP: True, DM: False}
        (d / "config.json").write_text(json.dumps({"groups_enabled": [GROUP]}))
        assert {c["jid"]: c["muted"] for c in _chats(d)} == {GROUP: False, DM: False}


def test_contacts_lists_the_address_book_of_each_account_by_name():
    with tmpdir() as d:
        _setup(d).c.commit()  # users.db's account rows
        _main_db(d, contacts=[("56933@s.whatsapp.net", "zoe"), ("56944@s.whatsapp.net", "Ana"),
                              ("77@lid", "Sin número"), ("56955@s.whatsapp.net", "")])
        out = wc.run_cli(["contacts"], d, d)
        assert out == {"contacts": [
            {"name": "Ana", "phone": "56944", "account": "personal", "label": "Personal"},
            {"name": "zoe", "phone": "56933", "account": "personal", "label": "Personal"}]}


def test_resolve_returns_the_jid_whatsapp_gives_or_an_error():
    with tmpdir() as d:
        _setup(d).c.commit()
        _main_db(d, contacts=[("56944444444@s.whatsapp.net", "Ana")])
        seen = []

        def post(acc, path, body):
            seen.append((acc, path, body))
            q = body["Phone"][0]
            return {"data": {"Users": [{"IsInWhatsapp": q == "56944444444", "JID": f"{q}:2@s.whatsapp.net", "Query": q}]}}
        assert wc.resolve(d, d, "personal", "+569 4444 4444", post) == {"jid": "56944444444@s.whatsapp.net",
                                                                        "name": "Ana"}
        assert seen == [("personal", "/user/check", {"Phone": ["56944444444"]})]
        assert "no está en WhatsApp" in wc.resolve(d, d, "personal", "56911111", post)["error"]
        assert "incompleto" in wc.resolve(d, d, "personal", "123", post)["error"]
        assert "sin conexión" in wc.resolve(d, d, "personal", "56911111", lambda *a: 1 / 0)["error"]
        lid = lambda acc, path, body: {"data": {"Users": [{"IsInWhatsapp": True, "JID": "status@broadcast"}]}}
        assert "error" in wc.resolve(d, d, "personal", "56911111", lid)


def test_enabled_group_is_not_muted_and_marker_clears_unread():
    with tmpdir() as d:
        db = _setup(d, groups_enabled=[GROUP])
        readstate.ensure(d, 0)
        db.add(GROUP, "buenas", sender="56922@s.whatsapp.net")
        readstate.mark(d, "personal", GROUP, 1)
        (c,) = _chats(d)
        assert not c["muted"] and c["unread"] == 0


def test_read_in_a_group_posts_one_call_per_sender_and_marks():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(GROUP, "a", sender="56911@s.whatsapp.net"); db.add(GROUP, "b", sender="56922@s.whatsapp.net")
        db.add(GROUP, "c", sender="56911@s.whatsapp.net")
        post = Post()
        assert wc.read_chat(d, d, "personal", GROUP, post) == {"ok": True}
        assert len(post.calls) == 2
        by = {c[2]["SenderPhone"]: c[2] for c in post.calls}
        assert by["56911@s.whatsapp.net"]["Id"] == ["M1", "M3"] and by["56922@s.whatsapp.net"]["Id"] == ["M2"]
        assert all(c[1] == "/chat/markread" and c[2]["ChatPhone"] == GROUP for c in post.calls)
        st = readstate.load(d)
        assert readstate.marker(st, "personal", GROUP) == 3 and st["pending"] == {}


def test_group_sender_on_lid_keeps_its_server_and_loses_the_device_suffix():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(GROUP, "a", sender="237786636488820:23@lid")
        post = Post()
        wc.read_chat(d, d, "personal", GROUP, post)
        assert post.calls[0][2]["SenderPhone"] == "237786636488820@lid"


def test_missing_read_json_baselines_at_the_current_last_id():
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "viejo")
        (c,) = _chats(d)
        assert c["unread"] == 0 and readstate.load(d)["since"] == 1
        db.add(DM, "nuevo")
        assert _chats(d)[0]["unread"] == 1


def test_read_with_no_read_json_does_not_bake_since_zero():
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "viejo")
        wc.read_chat(d, d, "personal", DM, Post())
        assert readstate.load(d)["since"] == 1


def test_offline_flag_and_backoff_after_group_fetch_failure():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(GROUP, "x", sender="56922@s.whatsapp.net")
        calls = []
        def boom(acc, path):
            calls.append(acc); raise OSError("down")
        assert wc.list_chats(d, d, Post(), get=boom)["offline"] is True
        n = len(calls)
        out = wc.list_chats(d, d, Post(), get=boom)
        assert len(calls) == n and out["offline"] is False
        assert wc.list_chats(d, d, Post(), get=_get())["offline"] is False


def test_retry_stops_after_first_connection_failure():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a"); db.add("56922@s.whatsapp.net", "b")
        wc.read_chat(d, d, "personal", DM, Post(fail=True))
        wc.read_chat(d, d, "personal", "56922@s.whatsapp.net", Post(fail=True))
        class Count(Post):
            def __call__(self, *a):
                self.n = getattr(self, "n", 0) + 1
                raise OSError("down")
        post = Count()
        out = wc.list_chats(d, d, post, get=_get())
        assert post.n == 1 and out["offline"] is True


def test_read_in_a_dm_has_no_sender_phone():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a")
        post = Post()
        wc.read_chat(d, d, "personal", DM, post)
        (c,) = post.calls
        assert c[2] == {"Id": ["M1"], "ChatPhone": DM}


def test_failed_post_is_pending_and_marker_still_rises_then_retried_on_next_list():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a")
        assert wc.read_chat(d, d, "personal", DM, Post(fail=True)) == {"ok": False, "pending": True}
        st = readstate.load(d)
        assert readstate.marker(st, "personal", DM) == 1 and len(st["pending"]) == 1
        post = Post()
        _chats(d, post=post)
        assert len(post.calls) == 1 and post.calls[0][2]["Id"] == ["M1"]
        assert readstate.load(d)["pending"] == {}


def test_pending_stays_when_retry_fails():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a")
        wc.read_chat(d, d, "personal", DM, Post(fail=True))
        _chats(d, post=Post(fail=True))
        assert len(readstate.load(d)["pending"]) == 1


def test_groups_cache_is_reused_within_six_hours_and_kept_on_network_failure():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(GROUP, "x", sender="56922@s.whatsapp.net")
        _chats(d, get=_get({GROUP: "Equipo"}))
        calls = []
        def boom(acc, path):
            calls.append(acc); raise OSError("down")
        assert _chats(d, get=boom)[0]["name"] == "Equipo" and calls == []
        cache = json.loads((d / "groups.json").read_text())
        cache["fetched"] = 0
        (d / "groups.json").write_text(json.dumps(cache))
        assert _chats(d, get=boom)[0]["name"] == "Equipo" and calls


def test_messages_page_adds_sender_name():
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "uno", push="Ana"); db.add(DM, "dos", push="Ana")
        out = wc.messages_page(d, d, "personal", DM, before=2)
        assert [m["text"] for m in out["messages"]] == ["uno"] and out["messages"][0]["sender_name"] == "Ana"
        assert out["more"] is False


def test_errors_become_json():
    with tmpdir() as d:
        db = _setup(d)
        db.c.execute("DROP TABLE message_history"); db.c.execute("CREATE TABLE message_history (id)"); db.c.commit()
        assert wc.run_cli(["chats"], d, d) == {"error": "esquema"}


def test_wuzapi_url_must_be_local():
    try:
        wc._url("http://evil.example/x", "/p")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_status_broadcast_is_not_listed_and_is_never_read():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "hola")
        db.add("status@broadcast", "mi estado", sender="56933@s.whatsapp.net", push="Beto")
        assert [c["jid"] for c in _chats(d)] == [DM]
        post = Post()
        out = wc.read_chat(d, d, "personal", "status@broadcast", post)
        assert out["ok"] is False and out["error"] and post.calls == []


class Http4xx(Post):
    def __call__(self, acc, path, body):
        import urllib.error
        from email.message import Message
        self.calls.append((acc, path, body))
        raise urllib.error.HTTPError("http://127.0.0.1:8080" + path, 400, "bad", Message(), None)


def test_retry_acks_only_what_was_clicked_never_what_arrived_after():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "visto"); top = db.add(DM, "visto 2")
        wc.read_chat(d, d, "personal", DM, Post(fail=True))
        assert readstate.load(d)["pending"] == {"personal:" + DM: top}
        db.add(DM, "llegó después")  # the user never saw this one
        post = Post()
        out = wc.list_chats(d, d, post, get=_get())
        assert [c[2]["Id"] for c in post.calls] == [["M1", "M2"]]
        assert out["offline"] is False and readstate.load(d)["pending"] == {}
        assert out["chats"][0]["unread"] == 1


def test_a_4xx_on_retry_drops_the_pending_read_and_is_not_offline():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a")
        wc.read_chat(d, d, "personal", DM, Post(fail=True))
        post = Http4xx()
        out = wc.list_chats(d, d, post, get=_get())
        assert len(post.calls) == 1 and out["offline"] is False
        assert readstate.load(d)["pending"] == {}
        wc.list_chats(d, d, post, get=_get())
        assert len(post.calls) == 1  # never re-acked


def test_a_locked_db_during_retry_does_not_abort_the_listing():
    import sqlite3
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a")
        wc.read_chat(d, d, "personal", DM, Post(fail=True))
        real, n = wc.history.connect, []
        def flaky(w):
            n.append(1)
            if len(n) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(w)
        wc.history.connect = flaky
        try:
            out = wc.list_chats(d, d, Post(), get=_get())
        finally:
            wc.history.connect = real
        assert [c["jid"] for c in out["chats"]] == [DM] and out["offline"] is False
        assert len(readstate.load(d)["pending"]) == 1  # retried on the next list


def test_a_pairing_dump_below_the_account_baseline_is_not_unread():
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "dump"); db.add("5692@s.whatsapp.net", "dump")
        readstate.baseline_account(d, d, "personal", "omarchy")
        assert [c["unread"] for c in _chats(d)] == [0, 0]
        db.add(DM, "nuevo")
        assert {c["jid"]: c["unread"] for c in _chats(d)}[DM] == 1


def _queue(d, jid, status="pending"):
    from sink import store
    store.upsert_queue(d, {"id": store.entry_id("personal", jid), "jid": jid, "account": "personal",
                           "push_name": "Ana", "text": "hola", "count": 1,
                           "ts": "2026-10-13T22:05:39-03:00", "status": status})


def test_reading_in_the_window_takes_the_chat_off_the_bar_counter():
    from sink import store
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a"); db.add("5692@s.whatsapp.net", "b")
        _queue(d, DM); _queue(d, "5692@s.whatsapp.net")
        wc.read_chat(d, d, "personal", DM, Post(fail=True))  # even when the phone never hears
        assert [e["jid"] for e in store.load_queue(d)] == ["5692@s.whatsapp.net"]


def test_reading_keeps_an_entry_whose_send_outcome_is_unknown():
    from sink import store
    with tmpdir() as d:
        db = _setup(d); readstate.ensure(d, 0)
        db.add(DM, "a")
        _queue(d, DM, status="unknown")
        wc.read_chat(d, d, "personal", DM, Post())
        assert [e["status"] for e in store.load_queue(d)] == ["unknown"]



def _avatar_env(d):
    import os
    os.environ["WA_STATE_ROOT"] = str(d)


def test_avatars_fetch_once_cache_none_and_space_out_the_asks():
    import os, urllib.error
    with tmpdir() as d:
        old = os.environ.get("WA_STATE_ROOT")
        _avatar_env(d)
        try:
            asks, sleeps = [], []

            def post(acc, path, body):
                asks.append((acc, path, body))
                if body["Phone"] == "sinfoto@lid":
                    raise urllib.error.HTTPError("u", 500, "no picture", {}, None)
                return {"data": {"url": "https://pps.whatsapp.net/v/x.jpg"}}
            keys = ["personal|56911@s.whatsapp.net", "personal|sinfoto@lid", "trabajo|569222"]
            out = wc.avatars(keys, post, download=lambda u: b"JPG", sleep=sleeps.append)
            assert out == {"fetched": ["personal|56911@s.whatsapp.net", "trabajo|569222"], "offline": False}
            assert asks[0] == ("personal", "/user/avatar", {"Phone": "56911@s.whatsapp.net", "Preview": True})
            assert sleeps == [wc.AVATAR_GAP] * 2
            assert wc.avatar_path("personal", "56911@s.whatsapp.net").read_bytes() == b"JPG"
            assert wc.avatar_path("personal", "sinfoto@lid").read_bytes() == b""
            asks.clear()
            assert wc.avatars(keys, post, download=lambda u: b"JPG", sleep=sleeps.append)["fetched"] == []
            assert asks == []  # all cached, the empty one too
            later = lambda: __import__("time").time() + wc.AVATAR_TTL + 1
            wc.avatars(keys[:1], post, download=lambda u: b"NEW", sleep=sleeps.append, now=later)
            assert wc.avatar_path("personal", "56911@s.whatsapp.net").read_bytes() == b"NEW"
        finally:
            os.environ.pop("WA_STATE_ROOT") if old is None else os.environ.__setitem__("WA_STATE_ROOT", old)


def test_avatars_stop_offline_refuse_other_hosts_and_bad_names():
    import os
    with tmpdir() as d:
        old = os.environ.get("WA_STATE_ROOT")
        _avatar_env(d)
        try:
            out = wc.avatars(["personal|a@lid", "personal|b@lid"], lambda *a: 1 / 0, sleep=lambda s: None)
            assert out == {"fetched": [], "offline": True}
            evil = lambda *a: {"data": {"url": "https://evil.example/x.jpg"}}
            assert wc.avatars(["personal|a@lid"], evil, sleep=lambda s: None)["offline"] is True
            assert not wc.avatar_path("personal", "a@lid").exists()
            assert wc.avatars(["../x|a@lid", "personal|..", "personal|/"], evil)["fetched"] == []
        finally:
            os.environ.pop("WA_STATE_ROOT") if old is None else os.environ.__setitem__("WA_STATE_ROOT", old)


if __name__ == "__main__":
    run(dict(globals()))
