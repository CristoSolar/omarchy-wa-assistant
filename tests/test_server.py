import io, json, sys, urllib.parse
from contextlib import redirect_stderr
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
import sink.server as server
from sink.server import handle_payload, purge_or_warn, response_status
from sink import store

PERSONAL = {"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"}
TRABAJO = {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo"}


def _write_accounts(d, accts):
    (d / "accounts.json").write_text(json.dumps(accts, ensure_ascii=False))


def _event(instance_name="omarchy", chat="56911111111@s.whatsapp.net",
           push_name="Ana", is_from_me=False, is_group=False, text="hola"):
    return {"instanceName": instance_name,
            "event": {"Info": {"Chat": chat, "IsFromMe": is_from_me,
                               "IsGroup": is_group, "PushName": push_name,
                               "Timestamp": "2026-09-22T16:00:00Z", "Type": "text"},
                      "Message": {"conversation": text}}}


EV = _event()


def test_json_payload_is_queued():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        assert handle_payload(json.dumps(EV).encode(), "application/json", d) is True
        q = store.load_queue(d)
        assert len(q) == 1 and q[0]["jid"] == "56911111111@s.whatsapp.net"
        assert q[0]["account"] == "personal"
        assert q[0]["id"] == store.entry_id("personal", "56911111111@s.whatsapp.net")


def test_form_encoded_payload_is_queued():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev_no_top_level_instance = {"event": EV["event"]}
        body = urllib.parse.urlencode(
            {"instanceName": "omarchy",
             "jsonData": json.dumps(ev_no_top_level_instance)}).encode()
        assert handle_payload(body, "application/x-www-form-urlencoded", d) is True
        assert len(store.load_queue(d)) == 1


def test_queued_message_also_lands_in_the_context_ring():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(EV).encode(), "application/json", d)
        ring = store.load_context(d, "56911111111@s.whatsapp.net", "personal")
        assert len(ring) == 1 and ring[0]["text"] == "hola"


def test_inbound_ring_entry_is_marked_with_an_explicit_direction():
    # Task 1: every ring entry gets an explicit "in"/"out" mark rather than
    # leaving direction to be guessed from "from" — a contact's display
    # name is not a safe signal (it can equal the user's own name).
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(EV).encode(), "application/json", d)
        ring = store.load_context(d, "56911111111@s.whatsapp.net", "personal")
        assert ring[0]["direction"] == "in"


def test_discarded_event_touches_nothing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event(is_from_me=True)
        assert handle_payload(json.dumps(ev).encode(), "application/json", d) is False
        assert store.load_queue(d) == []


def test_garbage_body_does_not_raise():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        assert handle_payload(b"<<<not json>>>", "application/json", d) is False


def test_an_unenabled_group_jid_is_logged_only_once():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        server._logged_group_jids.clear()
        ev = _event(is_group=True, chat="999@g.us")
        buf = io.StringIO()
        with redirect_stderr(buf):
            handle_payload(json.dumps(ev).encode(), "application/json", d)
            handle_payload(json.dumps(ev).encode(), "application/json", d)
        output = buf.getvalue()
        assert output.count("999@g.us") == 1, \
            "the same unenabled group JID must be logged only once per process"


def test_a_corrupt_wuzapi_db_does_not_stop_the_sink_from_starting():
    with tmpdir() as d:
        p = d / "users.db"
        p.write_bytes(b"this is not a sqlite database")
        assert purge_or_warn(p) == 0


# --- Task 2: account resolution ----------------------------------------


def test_event_for_a_known_account_queues_with_right_account_and_id():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL, TRABAJO])
        ev = _event(instance_name="trabajo")
        assert handle_payload(json.dumps(ev).encode(), "application/json", d) is True
        q = store.load_queue(d)
        assert len(q) == 1
        assert q[0]["account"] == "trabajo"
        assert q[0]["id"] == store.entry_id("trabajo", "56911111111@s.whatsapp.net")


def test_two_accounts_same_jid_produce_two_independent_entries_and_rings():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL, TRABAJO])
        jid = "56911111111@s.whatsapp.net"
        handle_payload(json.dumps(_event(instance_name="omarchy", text="hola personal"))
                       .encode(), "application/json", d)
        handle_payload(json.dumps(_event(instance_name="trabajo", text="hola trabajo"))
                       .encode(), "application/json", d)
        q = store.load_queue(d)
        assert len(q) == 2, "the same contact on two accounts must be two entries"
        ids = {e["id"] for e in q}
        assert ids == {store.entry_id("personal", jid), store.entry_id("trabajo", jid)}

        personal_ring = store.load_context(d, jid, "personal")
        trabajo_ring = store.load_context(d, jid, "trabajo")
        assert [m["text"] for m in personal_ring] == ["hola personal"]
        assert [m["text"] for m in trabajo_ring] == ["hola trabajo"]


def test_unknown_instance_name_queues_nothing_and_logs_once():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        server._logged_unknown_instances.clear()
        ev = _event(instance_name="ghost")
        buf = io.StringIO()
        with redirect_stderr(buf):
            for _ in range(3):
                assert handle_payload(json.dumps(ev).encode(),
                                      "application/json", d) is False
        assert store.load_queue(d) == []
        output = buf.getvalue()
        assert output.count("ghost") == 1, \
            "the same unrecognised instanceName must be logged only once per process"


def test_broken_accounts_json_raises_instead_of_queuing_under_a_guess():
    with tmpdir() as d:
        (d / "accounts.json").write_text("{not json")
        ev = _event(instance_name="omarchy")
        try:
            handle_payload(json.dumps(ev).encode(), "application/json", d)
            assert False, "expected StoreReadError to propagate"
        except store.StoreReadError:
            pass
        assert store.load_queue(d) == [], \
            "an event arriving while accounts.json is broken must not be queued"


# --- Fix round 1: 200 vs 500 --------------------------------------------


def test_broken_accounts_json_answers_500_and_queues_nothing():
    with tmpdir() as d:
        (d / "accounts.json").write_text("{not json")
        ev = _event(instance_name="omarchy")
        buf = io.StringIO()
        with redirect_stderr(buf):
            status = response_status(json.dumps(ev).encode(), "application/json", d)
        assert status == 500
        assert store.load_queue(d) == []


def test_own_message_answers_200_and_queues_nothing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event(is_from_me=True)
        assert response_status(json.dumps(ev).encode(), "application/json", d) == 200
        assert store.load_queue(d) == []


def test_unenabled_group_answers_200_and_queues_nothing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        server._logged_group_jids.clear()
        ev = _event(is_group=True, chat="888@g.us")
        assert response_status(json.dumps(ev).encode(), "application/json", d) == 200
        assert store.load_queue(d) == []


def test_unknown_instance_name_answers_200_and_queues_nothing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        server._logged_unknown_instances.clear()
        ev = _event(instance_name="ghost2")
        assert response_status(json.dumps(ev).encode(), "application/json", d) == 200
        assert store.load_queue(d) == []


def test_unparseable_body_answers_200_and_queues_nothing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        assert response_status(b"<<<not json>>>", "application/json", d) == 200
        assert store.load_queue(d) == []


def test_successful_queue_answers_200():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event(chat="56922222222@s.whatsapp.net")
        assert response_status(json.dumps(ev).encode(), "application/json", d) == 200
        assert len(store.load_queue(d)) == 1


# --- Task 1: attachment fields reach the queue entry --------------------


def test_image_attachment_fields_land_on_the_queue_entry():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event()
        ev["event"]["Message"] = {"imageMessage": {
            "URL": "https://mmg.whatsapp.net/x", "mimetype": "image/jpeg",
            "mediaKey": "bWVkaWFrZXk=", "fileSHA256": "c2hh",
            "fileEncSHA256": "ZW5jc2hh", "fileLength": 1234,
            "directPath": "/v/t62/abc"}}
        assert handle_payload(json.dumps(ev).encode(), "application/json", d) is True
        q = store.load_queue(d)
        assert q[0]["text"] == "[imagen]"
        assert q[0]["attachment"]["type"] == "image"
        assert q[0]["attachment"]["downloadable"] is True
        assert q[0]["attachment"]["mediaKey"] == "bWVkaWFrZXk="


def test_plain_text_message_queue_entry_has_no_attachment_field():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        assert handle_payload(json.dumps(EV).encode(), "application/json", d) is True
        q = store.load_queue(d)
        assert "attachment" not in q[0]


def test_incomplete_attachment_queues_marked_not_downloadable():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event()
        ev["event"]["Message"] = {"imageMessage": {"mimetype": "image/jpeg"}}
        assert handle_payload(json.dumps(ev).encode(), "application/json", d) is True
        q = store.load_queue(d)
        assert q[0]["attachment"] == {"type": "image", "downloadable": False}


def test_unexpected_exception_in_the_queueing_path_answers_500_not_crash():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event()

        def _boom(*a, **k):
            raise RuntimeError("disk exploded")

        original = store.upsert_queue
        store.upsert_queue = _boom
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                status = response_status(json.dumps(ev).encode(), "application/json", d)
            assert status == 500
            assert "disk exploded" in buf.getvalue()
        finally:
            store.upsert_queue = original


if __name__ == "__main__":
    run(dict(globals()))
