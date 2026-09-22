import json, sys, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink.server import handle_payload, purge_or_warn
from sink import store

EV = {"event": {"Info": {"Chat": "56911111111@s.whatsapp.net", "IsFromMe": False,
                         "IsGroup": False, "PushName": "Ana",
                         "Timestamp": "2026-09-22T16:00:00Z", "Type": "text"},
                "Message": {"conversation": "hola"}}}


def test_json_payload_is_queued():
    with tmpdir() as d:
        assert handle_payload(json.dumps(EV).encode(), "application/json", d) is True
        q = store.load_queue(d)
        assert len(q) == 1 and q[0]["jid"] == "56911111111@s.whatsapp.net"


def test_form_encoded_payload_is_queued():
    with tmpdir() as d:
        body = urllib.parse.urlencode({"instanceName": "spike",
                                       "jsonData": json.dumps(EV)}).encode()
        assert handle_payload(body, "application/x-www-form-urlencoded", d) is True
        assert len(store.load_queue(d)) == 1


def test_queued_message_also_lands_in_the_context_ring():
    with tmpdir() as d:
        handle_payload(json.dumps(EV).encode(), "application/json", d)
        ring = store.load_context(d, "56911111111@s.whatsapp.net")
        assert len(ring) == 1 and ring[0]["text"] == "hola"


def test_discarded_event_touches_nothing():
    with tmpdir() as d:
        ev = json.loads(json.dumps(EV))
        ev["event"]["Info"]["IsFromMe"] = True
        assert handle_payload(json.dumps(ev).encode(), "application/json", d) is False
        assert store.load_queue(d) == []


def test_garbage_body_does_not_raise():
    with tmpdir() as d:
        assert handle_payload(b"<<<not json>>>", "application/json", d) is False


def test_a_corrupt_wuzapi_db_does_not_stop_the_sink_from_starting():
    with tmpdir() as d:
        p = d / "users.db"
        p.write_bytes(b"this is not a sqlite database")
        assert purge_or_warn(p) == 0


if __name__ == "__main__":
    run(dict(globals()))
