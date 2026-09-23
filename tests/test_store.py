import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import store


def _entry(jid="56911111111@s.whatsapp.net", text="hola", name="Ana"):
    return {"id": jid, "jid": jid, "push_name": name, "text": text,
            "count": 1, "ts": "2026-09-22T10:00:00", "status": "pending"}


def test_upsert_collapses_repeat_messages_from_one_chat():
    with tmpdir() as d:
        store.upsert_queue(d, _entry(text="hola"))
        q = store.upsert_queue(d, _entry(text="estas?"))
        assert len(q) == 1, "same chat must not create a second entry"
        assert q[0]["text"] == "estas?", "entry must carry the latest text"
        assert q[0]["count"] == 2, "entry must count the unread messages"


def test_upsert_keeps_separate_chats_apart():
    with tmpdir() as d:
        store.upsert_queue(d, _entry(jid="1@s.whatsapp.net"))
        q = store.upsert_queue(d, _entry(jid="2@s.whatsapp.net"))
        assert len(q) == 2


def test_missing_queue_file_reads_as_empty():
    with tmpdir() as d:
        assert store.load_queue(d) == []


def test_corrupt_queue_file_raises_instead_of_reading_as_empty():
    # A file that EXISTS but is not valid JSON is a broken install, not an
    # empty queue — this is defect 2: read_json used to swallow this into
    # the same default as "no queue yet", so `wa-reply list` printed a
    # confident `[]` for a corrupt file. It must raise instead.
    with tmpdir() as d:
        (d / "queue.json").write_text("{not json")
        try:
            store.load_queue(d)
            assert False, "expected StoreReadError for invalid JSON"
        except store.StoreReadError as exc:
            assert "queue.json" in str(exc)


def test_unreadable_queue_file_raises_instead_of_reading_as_empty():
    # A file that EXISTS but cannot be opened (permissions — the root-owned
    # state file from defect 1) must be told apart from "no queue yet" too.
    import os
    with tmpdir() as d:
        p = d / "queue.json"
        p.write_text("[]")
        os.chmod(p, 0o000)
        try:
            if os.access(p, os.R_OK):
                # Running as root (or some other setup where permission bits
                # don't apply): nothing to test here.
                return
            try:
                store.load_queue(d)
                assert False, "expected StoreReadError for an unreadable file"
            except store.StoreReadError as exc:
                assert "queue.json" in str(exc)
        finally:
            os.chmod(p, 0o600)


def test_context_ring_stops_at_ten():
    with tmpdir() as d:
        jid = "56911111111@s.whatsapp.net"
        for i in range(25):
            ring = store.append_context(d, jid, {"text": f"m{i}"})
        assert len(ring) == 10
        assert ring[-1]["text"] == "m24", "newest message must be last"
        assert ring[0]["text"] == "m15", "oldest must be dropped"


def test_atomic_write_leaves_no_partial_file():
    with tmpdir() as d:
        p = d / "queue.json"
        store.atomic_write(p, json.dumps([{"id": "x"}]))
        assert json.loads(p.read_text())[0]["id"] == "x"
        assert list(d.glob("*.tmp*")) == [], "temp file must not survive"


def test_upsert_tolerates_an_entry_without_a_push_name():
    with tmpdir() as d:
        store.upsert_queue(d, _entry(name="Ana"))
        e2 = _entry(text="de nuevo")
        del e2["push_name"]
        q = store.upsert_queue(d, e2)
        assert q[0]["push_name"] == "Ana", "existing display name must survive a missing push_name"


def test_load_quick_replies_defaults_to_empty_list():
    with tmpdir() as d:
        assert store.load_quick_replies(d) == []


def test_load_quick_replies_reads_the_configured_list_without_disturbing_order():
    with tmpdir() as d:
        (d / "config.json").write_text(
            json.dumps({"groups_enabled": ["1@g.us"],
                       "quick_replies": ["Uno", "Dos", "Tres"]}))
        assert store.load_quick_replies(d) == ["Uno", "Dos", "Tres"]


def test_set_status_and_remove():
    with tmpdir() as d:
        e = _entry()
        store.upsert_queue(d, e)
        store.set_status(d, e["id"], "sending")
        assert store.load_queue(d)[0]["status"] == "sending"
        store.remove_queue(d, e["id"])
        assert store.load_queue(d) == []


def test_the_sink_data_dir_is_a_subdirectory_of_the_state_root():
    # I2: the sink's bind mount is WA_DATA_DIR, so it must not be the same
    # directory that holds secrets.env.
    import os
    previous_root = os.environ.get("WA_STATE_ROOT")
    previous_dir = os.environ.pop("WA_DATA_DIR", None)
    # The test harness (tests/runner.py) sets WA_SECRETS_FILE on every test
    # process so no test can ever fall back to the real secrets.env; that
    # override takes precedence over WA_STATE_ROOT by design (see
    # store.secrets_path()), so it must come off here too to observe the
    # plain state-root-derived default this test is actually about.
    previous_secrets_file = os.environ.pop("WA_SECRETS_FILE", None)
    with tmpdir() as d:
        os.environ["WA_STATE_ROOT"] = str(d)
        try:
            assert store.state_root() == d
            assert store.data_dir() == d / "state"
            assert store.secrets_path() == d / "secrets.env"
            assert store.secrets_path().parent != store.data_dir()
        finally:
            if previous_dir is not None:
                os.environ["WA_DATA_DIR"] = previous_dir
            if previous_root is None:
                os.environ.pop("WA_STATE_ROOT", None)
            else:
                os.environ["WA_STATE_ROOT"] = previous_root
            if previous_secrets_file is not None:
                os.environ["WA_SECRETS_FILE"] = previous_secrets_file


def test_the_secrets_path_has_its_own_override():
    import os
    previous_file = os.environ.get("WA_SECRETS_FILE")
    with tmpdir() as d:
        os.environ["WA_SECRETS_FILE"] = str(d / "elsewhere.env")
        try:
            assert store.secrets_path() == d / "elsewhere.env"
        finally:
            if previous_file is None:
                os.environ.pop("WA_SECRETS_FILE", None)
            else:
                os.environ["WA_SECRETS_FILE"] = previous_file


def test_a_new_message_does_not_clear_an_unconfirmed_send():
    # The "unknown" status is the durable half of the double-send guard.
    # An incoming message must not unlock it from the outside.
    with tmpdir() as d:
        store.upsert_queue(d, _entry())
        store.set_status(d, _entry()["id"], "unknown")
        q = store.upsert_queue(d, _entry(text="seguis ahi?"))
        assert q[0]["text"] == "seguis ahi?"
        assert q[0]["status"] == "unknown"


def test_a_new_message_does_reset_a_failed_entry_to_pending():
    with tmpdir() as d:
        store.upsert_queue(d, _entry())
        store.set_status(d, _entry()["id"], "failed")
        q = store.upsert_queue(d, _entry(text="seguis ahi?"))
        assert q[0]["status"] == "pending"


def test_a_new_message_does_not_clear_an_interrupted_send():
    # "sending" left behind by a wa-reply that was killed mid-POST is the
    # same ambiguity as "unknown"; an inbound message must not clear it.
    with tmpdir() as d:
        store.upsert_queue(d, _entry())
        store.set_status(d, _entry()["id"], "sending")
        q = store.upsert_queue(d, _entry(text="seguis ahi?"))
        assert q[0]["text"] == "seguis ahi?"
        assert q[0]["status"] == "sending"


if __name__ == "__main__":
    run(dict(globals()))
