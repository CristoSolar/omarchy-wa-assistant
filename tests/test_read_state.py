# tests/test_read_state.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import readstate

DM = "56911111111@s.whatsapp.net"


def test_absent_file_loads_empty_and_ensure_fixes_since():
    with tmpdir() as d:
        assert readstate.load(d) == {"since": 0, "chats": {}, "pending": {}}
        assert readstate.ensure(d, 40)["since"] == 40
        assert (d / "read.json").exists()


def test_ensure_never_moves_an_existing_since():
    with tmpdir() as d:
        readstate.ensure(d, 40)
        assert readstate.ensure(d, 99)["since"] == 40


def test_a_chat_without_a_marker_falls_back_to_since():
    with tmpdir() as d:
        state = readstate.ensure(d, 40)
        assert readstate.marker(state, "personal", DM) == 40


def test_mark_only_goes_up():
    with tmpdir() as d:
        readstate.ensure(d, 40)
        readstate.mark(d, "personal", DM, 50)
        readstate.mark(d, "personal", DM, 45)
        assert readstate.marker(readstate.load(d), "personal", DM) == 50
        readstate.mark(d, "personal", DM, 60)
        assert readstate.marker(readstate.load(d), "personal", DM) == 60


def test_markers_are_per_account():
    with tmpdir() as d:
        readstate.ensure(d, 10)
        readstate.mark(d, "personal", DM, 50)
        assert readstate.marker(readstate.load(d), "trabajo", DM) == 10


def test_pending_enters_and_leaves():
    with tmpdir() as d:
        readstate.ensure(d, 10)
        readstate.mark(d, "personal", DM, 11, pending=True)
        readstate.mark(d, "personal", DM, 11, pending=True)
        assert readstate.load(d)["pending"] == {"personal:" + DM: 11}
        readstate.mark(d, "personal", DM, 11, pending=False)
        assert readstate.load(d)["pending"] == {}


def test_pending_keeps_the_highest_top_clicked():
    with tmpdir() as d:
        readstate.ensure(d, 10)
        readstate.mark(d, "personal", DM, 12, pending=True)
        readstate.mark(d, "personal", DM, 15, pending=True)
        assert readstate.load(d)["pending"] == {"personal:" + DM: 15}


def test_the_old_pending_list_migrates_to_its_marker():
    import json
    with tmpdir() as d:
        key = "personal:" + DM
        (d / "read.json").write_text(json.dumps({"since": 10, "chats": {key: 33}, "pending": [key, "trabajo:x@g.us"]}))
        assert readstate.load(d)["pending"] == {key: 33, "trabajo:x@g.us": 10}
        readstate.mark(d, "personal", DM, 33, pending=False)
        assert readstate.load(d)["pending"] == {"trabajo:x@g.us": 10}


if __name__ == "__main__":
    run(globals())
