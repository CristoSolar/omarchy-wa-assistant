# tests/test_notify.py
import importlib.machinery, importlib.util, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run

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


if __name__ == "__main__":
    run(dict(globals()))
