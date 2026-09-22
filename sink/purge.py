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
    """Poll count_history until stable or timeout.

    Returns (final_count, settled) where settled=True if count was unchanged
    for stable_checks consecutive polls.

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

    while now_fn() - start < timeout:
        count = count_history(db_path)
        if count == prev_count:
            stable_count += 1
            if stable_count >= stable_checks:
                return (count, True)
        else:
            stable_count = 0
            prev_count = count

        sleep_fn(interval)

    return (prev_count if prev_count >= 0 else count_history(db_path), False)


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
        conn.execute("DELETE FROM message_history")
        conn.commit()
        conn.execute("VACUUM")
        return n
    finally:
        conn.close()
