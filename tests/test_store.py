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


def test_corrupt_queue_file_reads_as_empty():
    with tmpdir() as d:
        (d / "queue.json").write_text("{not json")
        assert store.load_queue(d) == []


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


def test_set_status_and_remove():
    with tmpdir() as d:
        e = _entry()
        store.upsert_queue(d, e)
        store.set_status(d, e["id"], "sending")
        assert store.load_queue(d)[0]["status"] == "sending"
        store.remove_queue(d, e["id"])
        assert store.load_queue(d) == []


if __name__ == "__main__":
    run(dict(globals()))
