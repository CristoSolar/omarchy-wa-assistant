"""Delete the WhatsApp history wuzapi stores on pairing.

The sync only fires when a device is paired, so this runs at startup and
again after pairing rather than on a timer.
"""
import sqlite3
import time
from pathlib import Path


def count_history(db_path) -> int:
    """Count rows in message_history table. Returns 0 if file or table is absent."""
    p = Path(db_path)
    if not p.exists():
        return 0
    conn = sqlite3.connect(p)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_history'")
        if cur.fetchone() is None:
            return 0
        return conn.execute("SELECT count(*) FROM message_history").fetchone()[0]
    finally:
        conn.close()


def wait_until_stable(db_path, stable_checks=3, interval=5.0, timeout=180.0,
                      sleep_fn=None, now_fn=None) -> tuple:
    """Poll count_history until the history dump has arrived and stopped growing.

    Returns ``(final_count, settled, saw_rows)``.

    - ``settled`` is True only when the count was unchanged for
      ``stable_checks`` consecutive polls **and** a non-zero count was seen
      at some point.
    - ``saw_rows`` is True if any poll ever observed at least one row.

    A count that has never been non-zero never counts as settled. ``loggedIn``
    flips at the end of the pairing handshake, *before* whatsmeow starts
    streaming history-sync blobs, so an empty table is ambiguous: it may mean
    the account has no history, or it may mean the dump has not begun. This
    function cannot tell those apart by looking at the database, so it keeps
    polling until ``timeout`` and hands the ambiguity back to the caller
    through ``saw_rows`` rather than reporting a premature "stable at zero".

    A missing database, or one with no ``message_history`` table, therefore
    polls for the full ``timeout`` and returns ``(0, False, False)`` — wuzapi
    may simply not have created the table yet.

    Args:
        db_path: path to SQLite database
        stable_checks: number of consecutive unchanged polls needed
        interval: seconds between polls
        timeout: maximum seconds to wait
        sleep_fn: injected sleep function (default: time.sleep)
        now_fn: injected time function (default: time.monotonic)
    """
    if sleep_fn is None:
        sleep_fn = time.sleep
    if now_fn is None:
        now_fn = time.monotonic

    start = now_fn()
    prev_count = -1
    stable_count = 0
    saw_rows = False
    count = 0

    while now_fn() - start < timeout:
        count = count_history(db_path)
        if count > 0:
            saw_rows = True
        if count == prev_count:
            stable_count += 1
            # Settling on a count that was never non-zero would mean
            # reporting "nothing to purge" before the dump even started.
            if saw_rows and stable_count >= stable_checks:
                return (count, True, True)
        else:
            stable_count = 0
            prev_count = count

        sleep_fn(interval)

    return (prev_count if prev_count >= 0 else count, False, saw_rows)


def purge_history(db_path) -> int:
    p = Path(db_path)
    if not p.exists():
        return 0
    conn = sqlite3.connect(p)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_history'")
        if cur.fetchone() is None:
            return 0
        n = conn.execute("SELECT count(*) FROM message_history").fetchone()[0]
        if n == 0:
            # Nothing to reclaim. VACUUM takes an exclusive lock on a
            # database wuzapi holds open, so do not pay for it for free.
            return 0
        conn.execute("DELETE FROM message_history")
        conn.commit()
        conn.execute("VACUUM")
        return n
    finally:
        conn.close()
