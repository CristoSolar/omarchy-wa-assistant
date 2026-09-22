# tests/test_events.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run
from sink.events import extract

TS = "2026-09-22T16:00:00Z"


def _ev(**info):
    base = {"Chat": "56911111111@s.whatsapp.net", "Sender": "56911111111@s.whatsapp.net",
            "IsFromMe": False, "IsGroup": False, "PushName": "Ana",
            "Timestamp": TS, "Type": "text"}
    base.update(info)
    return {"event": {"Info": base, "Message": {"conversation": "hola"}}}


def test_own_message_is_discarded():
    assert extract(_ev(IsFromMe=True), []) is None


def test_group_not_enabled_is_discarded():
    ev = _ev(Chat="123@g.us", IsGroup=True)
    assert extract(ev, []) is None


def test_enabled_group_passes():
    ev = _ev(Chat="123@g.us", IsGroup=True)
    out = extract(ev, ["123@g.us"])
    assert out is not None
    assert out["jid"] == "123@g.us"


def test_direct_message_passes_with_verbatim_jid():
    out = extract(_ev(), [])
    assert out["jid"] == "56911111111@s.whatsapp.net"
    assert out["text"] == "hola"
    assert out["push_name"] == "Ana"


def test_lid_addressed_chat_keeps_its_jid_verbatim():
    out = extract(_ev(Chat="237786636488820@lid"), [])
    assert out["jid"] == "237786636488820@lid", "never rewrite a @lid chat"


def test_extended_text_message_is_read():
    ev = _ev()
    ev["event"]["Message"] = {"extendedTextMessage": {"text": "con contexto"}}
    assert extract(ev, [])["text"] == "con contexto"


def test_audio_message_is_queued_as_placeholder():
    ev = _ev(Type="audio")
    ev["event"]["Message"] = {"audioMessage": {"seconds": 5}}
    assert extract(ev, [])["text"] == "[audio]"


def test_image_message_is_queued_as_placeholder():
    ev = _ev(Type="image")
    ev["event"]["Message"] = {"imageMessage": {}}
    assert extract(ev, [])["text"] == "[imagen]"


def test_unknown_message_shape_does_not_raise():
    ev = _ev(Type="weird")
    ev["event"]["Message"] = {"somethingNew": {"x": 1}}
    out = extract(ev, [])
    assert out is not None and out["text"] == "[mensaje no soportado]"


def test_missing_push_name_falls_back_to_the_jid():
    out = extract(_ev(PushName=""), [])
    assert out["push_name"] == "56911111111@s.whatsapp.net"


if __name__ == "__main__":
    run(dict(globals()))
