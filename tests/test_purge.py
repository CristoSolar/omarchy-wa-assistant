import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink.purge import purge_history, count_history, wait_until_stable, wuzapi_user_id


def _db(tmp_path, rows=3):
    p = tmp_path / "users.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE message_history (id INTEGER PRIMARY KEY, text_content TEXT)")
    c.executemany("INSERT INTO message_history (text_content) VALUES (?)",
                  [(f"secreto {i}",) for i in range(rows)])
    c.commit(); c.close()
    return p


def _multi_account_db(tmp_path, rows_by_user):
    """A message_history table carrying a `user_id` column, plus a `users`
    table mapping wuzapi user names to those ids — the real wuzapi shape,
    which the single-column `_db` helper above deliberately does not model
    (it exists only to exercise the whole-table, unscoped path)."""
    p = tmp_path / "users.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
    c.execute("CREATE TABLE message_history "
              "(id INTEGER PRIMARY KEY, user_id TEXT, text_content TEXT)")
    for name, (user_id, rows) in rows_by_user.items():
        c.execute("INSERT INTO users (id, name) VALUES (?, ?)", (user_id, name))
        c.executemany(
            "INSERT INTO message_history (user_id, text_content) VALUES (?, ?)",
            [(user_id, f"secreto {i}") for i in range(rows)])
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
            count, settled, saw_rows = wait_until_stable(
                p, stable_checks=2, interval=1.0, timeout=100.0,
                sleep_fn=mock_sleep, now_fn=mock_now)
            assert settled is True
            assert saw_rows is True
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
            count, settled, saw_rows = wait_until_stable(
                p, stable_checks=2, interval=1.0, timeout=10.0,
                sleep_fn=mock_sleep, now_fn=mock_now)
            assert settled is False
            assert saw_rows is True
            assert count > 0  # some messages were added
        finally:
            sink.purge.count_history = old_count


def test_wait_until_stable_on_missing_database_never_claims_it_settled():
    """A database that never appears is ambiguous, not settled-at-zero.

    wuzapi may not have created users.db yet. Reporting (0, True) here is
    exactly the premature settle that made wa-pair announce "table now
    empty" before the history dump had started.
    """
    with tmpdir() as d:
        calls = [0]
        def mock_sleep(_):
            calls[0] += 1
        def mock_now():
            return calls[0]

        count, settled, saw_rows = wait_until_stable(
            d / "nope.db", stable_checks=2, interval=1.0, timeout=10.0,
            sleep_fn=mock_sleep, now_fn=mock_now)
        assert count == 0
        assert settled is False
        assert saw_rows is False


def test_wait_until_stable_on_a_table_that_stays_empty_reports_no_rows_seen():
    """The C1 case: loggedIn flipped but WhatsApp has not sent anything yet.

    The table exists and is empty for the whole wait. Without saw_rows the
    caller cannot tell this apart from "this account genuinely has no
    history", and the old code called it settled after three polls.
    """
    with tmpdir() as d:
        p = _db(d, rows=0)
        ticks = [0]
        def mock_sleep(_):
            ticks[0] += 1
        def mock_now():
            return ticks[0]

        count, settled, saw_rows = wait_until_stable(
            p, stable_checks=3, interval=1.0, timeout=20.0,
            sleep_fn=mock_sleep, now_fn=mock_now)
        assert count == 0
        assert saw_rows is False, "no row was ever observed"
        assert settled is False, "a count that was never non-zero must not settle"
        assert ticks[0] >= 20, "it must keep waiting, not give up after stable_checks"


def test_wait_until_stable_when_rows_arrive_late_then_stop():
    """Empty at first, rows arrive, then the dump ends: saw_rows True, real count."""
    with tmpdir() as d:
        p = _db(d, rows=0)
        poll_num = [0]
        def mock_sleep(_):
            pass
        def mock_now():
            return poll_num[0]

        original_count_history = count_history

        def mock_count_history(db_path):
            poll_num[0] += 1
            # Polls 1-3 see an empty table; polls 4-6 add a row each; then
            # the dump stops and the count holds at 3.
            if 4 <= poll_num[0] <= 6:
                c = sqlite3.connect(db_path)
                c.execute("INSERT INTO message_history (text_content) VALUES (?)",
                          (f"late {poll_num[0]}",))
                c.commit()
                c.close()
            return original_count_history(db_path)

        import sink.purge
        old_count = sink.purge.count_history
        sink.purge.count_history = mock_count_history
        try:
            count, settled, saw_rows = wait_until_stable(
                p, stable_checks=2, interval=1.0, timeout=100.0,
                sleep_fn=mock_sleep, now_fn=mock_now)
            assert saw_rows is True
            assert settled is True
            assert count == 3
        finally:
            sink.purge.count_history = old_count


def test_purge_does_not_vacuum_an_empty_table():
    """VACUUM takes an exclusive lock on a DB wuzapi holds open; skip it for free."""
    with tmpdir() as d:
        p = _db(d, rows=0)
        vacuums = []
        real_connect = sqlite3.connect

        class _Spy:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *a):
                if "VACUUM" in sql.upper():
                    vacuums.append(sql)
                return self._conn.execute(sql, *a)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        import sink.purge
        sink.purge.sqlite3.connect = lambda *a, **k: _Spy(real_connect(*a, **k))
        try:
            assert purge_history(p) == 0
        finally:
            sink.purge.sqlite3.connect = real_connect
        assert vacuums == [], "an empty table must not be vacuumed"


# --- Multi-account: scoping a shared table to one account --------------


def test_wuzapi_user_id_finds_the_id_for_a_name():
    with tmpdir() as d:
        p = _multi_account_db(d, {"personal": ("id-1", 2), "trabajo": ("id-2", 5)})
        assert wuzapi_user_id(p, "trabajo") == "id-2"


def test_wuzapi_user_id_returns_none_for_an_unknown_name():
    with tmpdir() as d:
        p = _multi_account_db(d, {"personal": ("id-1", 2)})
        assert wuzapi_user_id(p, "nope") is None


def test_wuzapi_user_id_returns_none_on_a_missing_database():
    with tmpdir() as d:
        assert wuzapi_user_id(d / "nope.db", "personal") is None


def test_count_history_scoped_to_one_account_ignores_the_others():
    with tmpdir() as d:
        p = _multi_account_db(d, {"personal": ("id-1", 3), "trabajo": ("id-2", 7)})
        assert count_history(p, user_id="id-2") == 7
        assert count_history(p, user_id="id-1") == 3
        assert count_history(p) == 10  # unscoped: everyone


def test_wait_until_stable_scoped_ignores_another_accounts_concurrent_growth():
    """The bug this closes: pairing account two while account one is live.

    Account one's row count keeps growing on every poll (its own ordinary
    traffic, unrelated to the dump being waited on) while account two's
    count is already settled. Unscoped, that growth alone would stop the
    combined count from ever settling within the timeout. Scoped to
    account two's own wuzapi id, account one's growth is invisible and
    settling is detected immediately.
    """
    with tmpdir() as d:
        p = _multi_account_db(d, {"personal": ("id-1", 1), "trabajo": ("id-2", 4)})
        conn = sqlite3.connect(p)

        def mock_sleep(_):
            # Simulate account "personal" (id-1) receiving an ordinary
            # message on every poll, while "trabajo" (id-2) has already
            # finished its dump and is not written to again.
            conn.execute(
                "INSERT INTO message_history (user_id, text_content) VALUES (?, ?)",
                ("id-1", "msg"))
            conn.commit()

        poll_num = [0]
        def mock_now():
            return poll_num[0]
        real_count_history = count_history
        def counting_now(*a, **k):
            poll_num[0] += 1
            return poll_num[0]
        try:
            count, settled, saw_rows = wait_until_stable(
                p, stable_checks=2, interval=1.0, timeout=100.0,
                sleep_fn=mock_sleep, now_fn=counting_now, user_id="id-2")
        finally:
            conn.close()
        assert settled is True
        assert saw_rows is True
        assert count == 4  # trabajo's own count, never touched by personal's growth


if __name__ == "__main__":
    run(dict(globals()))
