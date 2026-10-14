# tests/test_read_state.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import readstate

DM = "56911111111@s.whatsapp.net"


def test_absent_file_loads_empty_and_ensure_fixes_since():
    with tmpdir() as d:
        assert readstate.load(d) == {"since": 0, "chats": {}, "pending": []}
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
        assert len(readstate.load(d)["pending"]) == 1
        readstate.mark(d, "personal", DM, 11, pending=False)
        assert readstate.load(d)["pending"] == []


if __name__ == "__main__":
    run(globals())
