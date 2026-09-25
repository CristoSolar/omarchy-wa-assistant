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


def test_own_message_never_queues_as_incoming():
    # Task 1: an own-device message is recorded in the ring and can clear a
    # queue entry (see the dedicated tests below), but it must never itself
    # become a NEW queue entry - it is the owner's own side, not something
    # to answer.
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event(is_from_me=True)
        handle_payload(json.dumps(ev).encode(), "application/json", d)
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


# --- Task 1: own-device messages and read receipts -----------------------


def _receipt(state, instance_name="omarchy",
            chat="56911111111@s.whatsapp.net"):
    return {"instanceName": instance_name, "type": "ReadReceipt", "state": state,
            "event": {"Chat": chat, "Sender": chat, "IsFromMe": False,
                     "IsGroup": False, "MessageIDs": ["ABC123"],
                     "Timestamp": "2026-09-22T16:05:00Z", "Type": "read"}}


def test_own_device_message_lands_in_the_ring_marked_outgoing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event(is_from_me=True, text="ya le contesté desde el celular")
        assert handle_payload(json.dumps(ev).encode(), "application/json", d) is True
        ring = store.load_context(d, "56911111111@s.whatsapp.net", "personal")
        assert len(ring) == 1
        assert ring[0]["direction"] == "out"
        assert ring[0]["text"] == "ya le contesté desde el celular"


def test_own_device_message_clears_that_chats_queue_entry():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        assert len(store.load_queue(d)) == 1
        ev = _event(is_from_me=True, text="contestado desde el teléfono")
        handle_payload(json.dumps(ev).encode(), "application/json", d)
        assert store.load_queue(d) == []


def test_plugins_own_echo_does_not_duplicate_the_ring_entry():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        jid = "56911111111@s.whatsapp.net"
        # bin/wa-reply already wrote this exact "out" entry after a
        # confirmed send, before the echo of that same send reaches us.
        store.append_context(d, jid, {"direction": "out", "text": "ok, gracias",
                                      "ts": "2026-09-22T16:01:00Z"}, "personal")
        ev = _event(text="ok, gracias")
        ev["event"]["Info"]["IsFromMe"] = True
        handle_payload(json.dumps(ev).encode(), "application/json", d)
        ring = store.load_context(d, jid, "personal")
        assert len(ring) == 1, "the echo of the plugin's own send must not duplicate"


def test_a_later_message_with_the_same_words_is_still_recorded():
    # The echo check only compares against the ring's OWN most recent
    # entry. As soon as anything else lands in between - here, another
    # inbound message from the contact - a later own message that happens
    # to reuse the same words is no longer adjacent to a matching "out"
    # entry, so it must be recorded rather than silently swallowed as if it
    # were the earlier send's echo.
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        jid = "56911111111@s.whatsapp.net"
        own = _event(text="ok")
        own["event"]["Info"]["IsFromMe"] = True
        handle_payload(json.dumps(own).encode(), "application/json", d)
        handle_payload(json.dumps(_event(text="¿en serio?")).encode(),
                       "application/json", d)
        handle_payload(json.dumps(own).encode(), "application/json", d)
        ring = store.load_context(d, jid, "personal")
        out_texts = [m["text"] for m in ring if m["direction"] == "out"]
        assert out_texts == ["ok", "ok"], \
            "a genuine repeat separated by another message must be recorded"


def test_own_device_messages_still_respect_the_ring_cap():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        jid = "56911111111@s.whatsapp.net"
        for i in range(store.CONTEXT_LIMIT + 5):
            ev = _event(is_from_me=True, text=f"msg {i}")
            handle_payload(json.dumps(ev).encode(), "application/json", d)
        ring = store.load_context(d, jid, "personal")
        assert len(ring) == store.CONTEXT_LIMIT
        assert ring[-1]["text"] == f"msg {store.CONTEXT_LIMIT + 4}"


def test_read_self_receipt_clears_that_chats_queue_entry():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        assert len(store.load_queue(d)) == 1
        handle_payload(json.dumps(_receipt("ReadSelf")).encode(), "application/json", d)
        assert store.load_queue(d) == []


def test_plain_read_receipt_does_not_clear_the_queue():
    # This is the trap: a contact opening the owner's own message must not
    # empty the very entry that is waiting for a reply.
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        assert len(store.load_queue(d)) == 1
        handle_payload(json.dumps(_receipt("Read")).encode(), "application/json", d)
        assert len(store.load_queue(d)) == 1


def test_read_receipt_for_a_chat_with_no_entry_does_nothing():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        assert handle_payload(json.dumps(_receipt("ReadSelf")).encode(),
                              "application/json", d) is False
        assert store.load_queue(d) == []


def test_unknown_status_entry_survives_an_own_device_message():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        eid = store.entry_id("personal", "56911111111@s.whatsapp.net")
        store.set_status(d, eid, "unknown")
        ev = _event(is_from_me=True, text="respondido")
        handle_payload(json.dumps(ev).encode(), "application/json", d)
        q = store.load_queue(d)
        assert len(q) == 1 and q[0]["status"] == "unknown"


def test_sending_status_entry_survives_a_read_self_receipt():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        eid = store.entry_id("personal", "56911111111@s.whatsapp.net")
        store.set_status(d, eid, "sending")
        handle_payload(json.dumps(_receipt("ReadSelf")).encode(), "application/json", d)
        q = store.load_queue(d)
        assert len(q) == 1 and q[0]["status"] == "sending"


def test_unknown_status_entry_survives_a_read_self_receipt():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        eid = store.entry_id("personal", "56911111111@s.whatsapp.net")
        store.set_status(d, eid, "unknown")
        handle_payload(json.dumps(_receipt("ReadSelf")).encode(), "application/json", d)
        q = store.load_queue(d)
        assert len(q) == 1 and q[0]["status"] == "unknown"


def test_sending_status_entry_survives_an_own_device_message():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        handle_payload(json.dumps(_event()).encode(), "application/json", d)
        eid = store.entry_id("personal", "56911111111@s.whatsapp.net")
        store.set_status(d, eid, "sending")
        ev = _event(is_from_me=True, text="respondido")
        handle_payload(json.dumps(ev).encode(), "application/json", d)
        q = store.load_queue(d)
        assert len(q) == 1 and q[0]["status"] == "sending"


def test_own_device_message_from_an_unknown_instance_is_dropped_and_logged_once():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        server._logged_unknown_instances.clear()
        ev = _event(instance_name="ghost3", is_from_me=True)
        buf = io.StringIO()
        with redirect_stderr(buf):
            handle_payload(json.dumps(ev).encode(), "application/json", d)
            handle_payload(json.dumps(ev).encode(), "application/json", d)
        assert store.load_context(d, "56911111111@s.whatsapp.net", "personal") == []
        assert buf.getvalue().count("ghost3") == 1


def test_read_receipt_from_an_unknown_instance_is_dropped_and_logged_once():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        server._logged_unknown_instances.clear()
        ev = _receipt("ReadSelf", instance_name="ghost4")
        buf = io.StringIO()
        with redirect_stderr(buf):
            handle_payload(json.dumps(ev).encode(), "application/json", d)
            handle_payload(json.dumps(ev).encode(), "application/json", d)
        assert buf.getvalue().count("ghost4") == 1


def test_own_device_message_answers_200():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        ev = _event(is_from_me=True, text="desde el teléfono")
        assert response_status(json.dumps(ev).encode(), "application/json", d) == 200


def test_read_receipt_answers_200():
    with tmpdir() as d:
        _write_accounts(d, [PERSONAL])
        status = response_status(json.dumps(_receipt("Read")).encode(),
                                 "application/json", d)
        assert status == 200


if __name__ == "__main__":
    run(dict(globals()))
