import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink.purge import purge_history, count_history, wait_until_stable


def _db(tmp_path, rows=3):
    p = tmp_path / "users.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE message_history (id INTEGER PRIMARY KEY, text_content TEXT)")
    c.executemany("INSERT INTO message_history (text_content) VALUES (?)",
                  [(f"secreto {i}",) for i in range(rows)])
    c.commit(); c.close()
    return p


def test_purge_deletes_every_row():
    with tmpdir() as d:
        p = _db(d, rows=5)
        assert purge_history(p) == 5
        c = sqlite3.connect(p)
        assert c.execute("SELECT count(*) FROM message_history").fetchone()[0] == 0
        c.close()


def test_purge_on_missing_file_is_a_noop():
    with tmpdir() as d:
        assert purge_history(d / "nope.db") == 0


def test_purge_on_missing_table_is_a_noop():
    with tmpdir() as d:
        p = d / "users.db"
        sqlite3.connect(p).close()
        assert purge_history(p) == 0


def test_count_history_on_missing_file():
    with tmpdir() as d:
        assert count_history(d / "nope.db") == 0


def test_count_history_on_missing_table():
    with tmpdir() as d:
        p = d / "users.db"
        sqlite3.connect(p).close()
        assert count_history(p) == 0


def test_wait_until_stable_when_count_grows_then_stops():
    """A count that grows then stabilizes returns settled=True with the final count."""
    with tmpdir() as d:
        p = _db(d, rows=5)
        poll_num = [0]
        def mock_sleep(_):
            pass
        def mock_now():
            return poll_num[0]

        # Simulate history still being written: insert rows on first two polls
        original_count_history = count_history
        def mock_count_history(db_path):
            poll_num[0] += 1
            if poll_num[0] <= 2:
                c = sqlite3.connect(db_path)
                c.execute("INSERT INTO message_history (text_content) VALUES (?)", (f"new {poll_num[0]}",))
                c.commit()
                c.close()
            return original_count_history(db_path)

        import sink.purge
        old_count = sink.purge.count_history
        sink.purge.count_history = mock_count_history
        try:
            count, settled = wait_until_stable(p, stable_checks=2, interval=1.0, timeout=100.0,
                                               sleep_fn=mock_sleep, now_fn=mock_now)
            assert settled is True
            assert count == 7  # started with 5, added 2
        finally:
            sink.purge.count_history = old_count


def test_wait_until_stable_when_count_keeps_growing():
    """A count that keeps growing until timeout returns settled=False."""
    with tmpdir() as d:
        p = _db(d, rows=0)
        poll_num = [0]
        def mock_sleep(_):
            pass
        def mock_now():
            return poll_num[0]

        # Always insert, so count keeps growing
        def mock_count_history(db_path):
            poll_num[0] += 1
            c = sqlite3.connect(db_path)
            c.execute("INSERT INTO message_history (text_content) VALUES (?)", (f"msg {poll_num[0]}",))
            c.commit()
            c.close()
            return poll_num[0]

        import sink.purge
        old_count = sink.purge.count_history
        sink.purge.count_history = mock_count_history
        try:
            count, settled = wait_until_stable(p, stable_checks=2, interval=1.0, timeout=10.0,
                                               sleep_fn=mock_sleep, now_fn=mock_now)
            assert settled is False
            assert count > 0  # some messages were added
        finally:
            sink.purge.count_history = old_count


def test_wait_until_stable_on_missing_database():
    """A missing database returns (0, True) immediately."""
    with tmpdir() as d:
        calls = [0]
        def mock_sleep(_):
            calls[0] += 1
        def mock_now():
            return calls[0]

        count, settled = wait_until_stable(d / "nope.db", stable_checks=2, interval=1.0, timeout=100.0,
                                           sleep_fn=mock_sleep, now_fn=mock_now)
        assert count == 0
        assert settled is True
        # Should return quickly without many polls
        assert calls[0] < 5


if __name__ == "__main__":
    run(dict(globals()))
