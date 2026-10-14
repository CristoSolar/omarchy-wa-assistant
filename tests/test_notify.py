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


def _ok(msg, jid=DM, enabled=(), focused="", acc="personal"):
    return wn.should_notify(msg, acc, jid, enabled_groups=set(enabled), focused=focused, our_ids=OURS)


def test_dm_notifies():
    assert _ok(_msg())


def test_own_message_does_not():
    assert not _ok(_msg(from_me=True))


def test_focused_chat_does_not():
    assert not _ok(_msg(), focused=f"personal:{DM}")
    assert _ok(_msg(), focused=f"trabajo:{DM}")
    assert _ok(_msg(), focused="")


def test_group_not_enabled_is_silent():
    assert not _ok(_msg(), jid=GROUP)


def test_group_enabled_notifies():
    assert _ok(_msg(), jid=GROUP, enabled=[GROUP])


def test_mention_in_disabled_group_notifies_even_with_device_suffix():
    assert _ok(_msg(mentions=["56900000001:12@s.whatsapp.net"]), jid=GROUP)
    assert _ok(_msg(mentions=["99999@lid"]), jid=GROUP)
    assert not _ok(_msg(mentions=["5698@s.whatsapp.net"]), jid=GROUP)


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


class Fake:
    def __init__(self, fail_on=None):
        self.shown, self.fail_on = [], fail_on

    def show(self, key, title, text):
        if text == self.fail_on:
            raise OSError("notify-send gone")
        self.shown.append((key, text))


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
            wn.process(d, d, cur, n)
        finally:
            wn.should_notify = real
        assert [t for _, t in n.shown] == ["uno", "tres"]
        assert cur["id"] == last > bad


def test_a_failing_show_does_not_repeat_or_block_other_chats():
    with tmpdir() as d:
        db = _setup(d)
        db.add(DM, "uno"); last = db.add("5692@s.whatsapp.net", "dos")
        cur, n = {"id": 0}, Fake(fail_on="uno")
        wn.process(d, d, cur, n)
        assert [t for _, t in n.shown] == ["dos"] and cur["id"] == last
        wn.process(d, d, cur, n)
        assert len(n.shown) == 1


if __name__ == "__main__":
    run(dict(globals()))
