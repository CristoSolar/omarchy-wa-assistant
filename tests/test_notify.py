# tests/test_notify.py
import importlib.machinery, importlib.util, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
from tests.runner import run, tmpdir
from tests.test_history import Db

_p = Path(__file__).resolve().parents[1] / "bin" / "wa-notify"
_loader = importlib.machinery.SourceFileLoader("wa_notify", str(_p))
_spec = importlib.util.spec_from_loader("wa_notify", _loader)
wn = importlib.util.module_from_spec(_spec)
_loader.exec_module(wn)

DM = "56911111111@s.whatsapp.net"
GROUP = "120363000000000001@g.us"
OURS = {"56900000001@s.whatsapp.net", "99999@lid"}


def _msg(**kw):
    m = {"id": 1, "message_id": "M", "from_me": False, "sender": DM, "push_name": "Ana",
         "ts": "", "text": "hola", "mentions": []}
    m.update(kw)
    return m


def _ok(msg, jid=DM, enabled=(), focused="", acc="personal", muted=()):
    return wn.should_notify(msg, acc, jid, enabled_groups=set(enabled), focused=focused,
                            our_ids=OURS, muted=set(muted))


def test_dm_notifies():
    assert _ok(_msg())


def test_own_message_does_not():
    assert not _ok(_msg(from_me=True))


def test_focused_chat_does_not():
    assert not _ok(_msg(), focused=f"personal:{DM}")
    assert _ok(_msg(), focused=f"trabajo:{DM}")
    assert _ok(_msg(), focused="")


def test_group_notifies_like_the_phone():
    assert _ok(_msg(), jid=GROUP)


def test_chat_muted_on_the_phone_is_silent():
    assert not _ok(_msg(), jid=GROUP, muted=[GROUP])
    assert not _ok(_msg(), muted=[DM])


def test_group_enabled_notifies_even_if_muted():
    assert _ok(_msg(), jid=GROUP, enabled=[GROUP], muted=[GROUP])


def test_mention_in_muted_group_notifies_even_with_device_suffix():
    assert _ok(_msg(mentions=["56900000001:12@s.whatsapp.net"]), jid=GROUP, muted=[GROUP])
    assert _ok(_msg(mentions=["99999@lid"]), jid=GROUP, muted=[GROUP])
    assert not _ok(_msg(mentions=["5698@s.whatsapp.net"]), jid=GROUP, muted=[GROUP])


def test_mention_does_not_override_focus_or_own():
    m = _msg(mentions=["99999@lid"])
    assert not _ok(m, jid=GROUP, focused=f"personal:{GROUP}")
    assert not _ok(dict(m, from_me=True), jid=GROUP)


def test_body_truncates_to_80():
    b = wn.body(_msg(text="x" * 200))
    assert len(b) == 80 and b.endswith("…")
    assert wn.body(_msg(text="x" * 80)) == "x" * 80


def test_body_attachments():
    assert wn.body(_msg(text="", attachment={"type": "image", "downloadable": True})) == "📷 Foto"
    assert wn.body(_msg(text="", attachment={"type": "audio", "downloadable": True})) == "🎤 Audio"
    assert wn.body(_msg(text="", attachment={"type": "document", "downloadable": True})) == "📎 Archivo"
    assert wn.body(_msg(text="mira", attachment={"type": "image", "downloadable": True})) == "mira"
    assert wn.body(_msg(text="", attachment={"type": "ubicación", "downloadable": False})) == "📍 Ubicación"
    assert wn.body(_msg(text="", attachment={"type": "contacto", "downloadable": False})) == "👤 Contacto"


def test_body_of_a_real_attachment_row_uses_the_label_not_the_placeholder():
    # history gives an uncaptioned image the text "[imagen]"
    assert wn.body(_msg(text="[imagen]", attachment={"type": "image", "downloadable": True})) == "📷 Foto"
    assert wn.body(_msg(text="[ubicación]", attachment={"type": "ubicación", "downloadable": False})) == "📍 Ubicación"


class Fake:
    def __init__(self, fail_on=None):
        self.shown, self.fail_on = [], fail_on

    def show(self, key, title, text):
        if text == self.fail_on:
            raise OSError("notify-send gone")
        self.shown.append((key, text))


NOW = 1791939939.0  # tests.test_history's default Info.Timestamp, 2026-10-13T22:05:39-03:00


def _setup(d):
    (d / "accounts.json").write_text(json.dumps([{"id": "personal", "label": "P", "wuzapi_user": "omarchy"}]))
    (d / "config.json").write_text(json.dumps({"groups_enabled": []}))
    return Db(d)


def test_a_row_that_raises_does_not_stall_later_rows_and_cursor_passes_it():
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "uno"); bad = db.add(DM, "boom"); last = db.add("5692@s.whatsapp.net", "tres")
        real, calls = wn.should_notify, []

        def flaky(msg, *a, **k):
            if msg["text"] == "boom":
                raise KeyError("bad row")
            return real(msg, *a, **k)
        wn.should_notify = flaky
        try:
            cur, n = {"id": 0}, Fake()
            wn.process(d, d, cur, n, now=NOW)
        finally:
            wn.should_notify = real
        assert [t for _, t in n.shown] == ["uno", "tres"]
        assert cur["id"] == last > bad


def test_a_failing_show_does_not_repeat_or_block_other_chats():
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "uno"); last = db.add("5692@s.whatsapp.net", "dos")
        cur, n = {"id": 0}, Fake(fail_on="uno")
        wn.process(d, d, cur, n, now=NOW)
        assert [t for _, t in n.shown] == ["dos"] and cur["id"] == last
        wn.process(d, d, cur, n, now=NOW)
        assert len(n.shown) == 1


def test_a_status_post_never_notifies():
    with tmpdir() as d:
        db = _setup(d)
        db.add("status@broadcast", "mi estado", sender="56933@s.whatsapp.net")
        last = db.add(DM, "hola")
        cur, n = {"id": 0}, Fake()
        wn.process(d, d, cur, n, now=NOW)
        assert [(k[1], t) for k, t in n.shown] == [(DM, "hola")] and cur["id"] == last


def test_rows_older_than_ten_minutes_never_notify_and_the_cursor_passes_them():
    # A pairing dumps the whole history with IsFromMe=false and ids above
    # the cursor; none of it is news.
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "viejo", ts="2026-10-13T21:55:38-03:00")      # 10 min 1 s
        db.add("5692@s.whatsapp.net", "reciente", ts="2026-10-13T21:56:39-03:00")
        last = db.add("5693@s.whatsapp.net", "rara", ts="no es una fecha")
        cur, n = {"id": 0}, Fake()
        wn.process(d, d, cur, n, now=NOW)
        assert sorted(t for _, t in n.shown) == ["rara", "reciente"] and cur["id"] == last



def test_without_now_the_age_filter_still_applies():
    # main() calls process() without `now`; the filter must not go dead.
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "viejo", ts="2020-01-01T00:00:00-03:00")
        cur, n = {"id": 0}, Fake()
        wn.process(d, d, cur, n)
        assert n.shown == []


if __name__ == "__main__":
    run(dict(globals()))
