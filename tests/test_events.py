# tests/test_events.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run
from sink.events import describe_skipped_group, extract

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


def test_describe_skipped_group_reports_jid_and_sender_for_unenabled_group():
    ev = _ev(Chat="123@g.us", IsGroup=True, PushName="Familia")
    out = describe_skipped_group(ev, [])
    assert out == {"jid": "123@g.us", "sender": "Familia"}


def test_describe_skipped_group_is_none_for_an_enabled_group():
    ev = _ev(Chat="123@g.us", IsGroup=True)
    assert describe_skipped_group(ev, ["123@g.us"]) is None


def test_describe_skipped_group_is_none_for_a_direct_message():
    assert describe_skipped_group(_ev(), []) is None


def test_describe_skipped_group_is_none_for_the_users_own_group_message():
    ev = _ev(Chat="123@g.us", IsGroup=True, IsFromMe=True)
    assert describe_skipped_group(ev, []) is None


def test_an_empty_conversation_string_is_not_treated_as_text():
    # An empty body produced a queue entry with nothing in it, which the
    # user cannot act on and cannot tell apart from a real short message.
    e = extract({"event": {"Info": {"Chat": "1@s.whatsapp.net"},
                           "Message": {"conversation": ""}}}, [])
    assert e["text"] == "[mensaje no soportado]"


def test_an_empty_conversation_still_falls_through_to_a_placeholder():
    e = extract({"event": {"Info": {"Chat": "1@s.whatsapp.net"},
                           "Message": {"conversation": "",
                                       "audioMessage": {}}}}, [])
    assert e["text"] == "[audio]"


# --- Task 1: attachment fields for a later download ---------------------

FULL_MEDIA_FIELDS = {
    "URL": "https://mmg.whatsapp.net/x", "mimetype": "image/jpeg",
    "mediaKey": "bWVkaWFrZXk=", "fileSHA256": "c2hh",
    "fileEncSHA256": "ZW5jc2hh", "fileLength": 1234,
    "directPath": "/v/t62/abc",
}


def test_text_message_gains_no_attachment_field():
    out = extract(_ev(), [])
    assert "attachment" not in out


def test_image_message_stores_the_fields_the_download_endpoint_requires():
    ev = _ev()
    ev["event"]["Message"] = {"imageMessage": dict(FULL_MEDIA_FIELDS)}
    out = extract(ev, [])
    assert out["text"] == "[imagen]"
    att = out["attachment"]
    assert att["type"] == "image"
    assert att["downloadable"] is True
    for field, value in FULL_MEDIA_FIELDS.items():
        assert att[field] == value


def test_audio_message_stores_the_fields_the_download_endpoint_requires():
    ev = _ev()
    ev["event"]["Message"] = {"audioMessage": dict(FULL_MEDIA_FIELDS)}
    out = extract(ev, [])
    att = out["attachment"]
    assert att["type"] == "audio" and att["downloadable"] is True
    for field, value in FULL_MEDIA_FIELDS.items():
        assert att[field] == value


def test_video_message_stores_the_fields_the_download_endpoint_requires():
    ev = _ev()
    ev["event"]["Message"] = {"videoMessage": dict(FULL_MEDIA_FIELDS)}
    out = extract(ev, [])
    att = out["attachment"]
    assert att["type"] == "video" and att["downloadable"] is True
    for field, value in FULL_MEDIA_FIELDS.items():
        assert att[field] == value


def test_sticker_message_stores_the_fields_the_download_endpoint_requires():
    ev = _ev()
    ev["event"]["Message"] = {"stickerMessage": dict(FULL_MEDIA_FIELDS)}
    out = extract(ev, [])
    att = out["attachment"]
    assert att["type"] == "sticker" and att["downloadable"] is True
    for field, value in FULL_MEDIA_FIELDS.items():
        assert att[field] == value


def test_document_message_stores_the_fields_the_download_endpoint_requires():
    ev = _ev()
    ev["event"]["Message"] = {"documentMessage": dict(FULL_MEDIA_FIELDS)}
    out = extract(ev, [])
    att = out["attachment"]
    assert att["type"] == "document" and att["downloadable"] is True
    for field, value in FULL_MEDIA_FIELDS.items():
        assert att[field] == value


def test_image_message_missing_a_required_field_is_not_downloadable():
    fields = dict(FULL_MEDIA_FIELDS)
    del fields["mediaKey"]
    ev = _ev()
    ev["event"]["Message"] = {"imageMessage": fields}
    out = extract(ev, [])
    assert out["text"] == "[imagen]", "the placeholder must be unchanged"
    att = out["attachment"]
    assert att == {"type": "image", "downloadable": False}, \
        "a partial field set must not leak any of the fields it did have"


def test_image_message_with_no_fields_at_all_is_not_downloadable():
    ev = _ev()
    ev["event"]["Message"] = {"imageMessage": {}}
    out = extract(ev, [])
    assert out["attachment"] == {"type": "image", "downloadable": False}


def test_location_message_is_never_downloadable():
    ev = _ev()
    ev["event"]["Message"] = {"locationMessage": {"degreesLatitude": 1.0}}
    out = extract(ev, [])
    assert out["text"] == "[ubicación]"
    assert out["attachment"]["downloadable"] is False


def test_contact_message_is_never_downloadable():
    ev = _ev()
    ev["event"]["Message"] = {"contactMessage": {"displayName": "x"}}
    out = extract(ev, [])
    assert out["text"] == "[contacto]"
    assert out["attachment"]["downloadable"] is False


if __name__ == "__main__":
    run(dict(globals()))
