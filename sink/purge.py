"""Delete the WhatsApp history wuzapi stores on pairing.

The sync only fires when a device is paired, so this runs at startup and
again after pairing rather than on a timer.
"""
import sqlite3
import time
from pathlib import Path


def count_history(db_path, user_id=None) -> int:
    """Count rows in message_history table. Returns 0 if file or table is absent.

    `user_id` scopes the count to one wuzapi user (the DB's own `id`, not
    the account name) via the `user_id` column wuzapi's message_history
    table carries. Every account's traffic — not just the history-sync
    dump, ordinary messages too — lands in this one shared table, so with
    two accounts a second account's history-sync arriving while a first
    account is already live and sending/receiving normally would otherwise
    be indistinguishable from that first account's ordinary, unrelated
    growth. `None` (the default) counts every row, which is what verifying
    a full-table purge needs.
    """
    p = Path(db_path)
    if not p.exists():
        return 0
    conn = sqlite3.connect(p)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_history'")
        if cur.fetchone() is None:
            return 0
        if user_id is None:
            return conn.execute("SELECT count(*) FROM message_history").fetchone()[0]
        return conn.execute(
            "SELECT count(*) FROM message_history WHERE user_id = ?",
            (user_id,)).fetchone()[0]
    finally:
        conn.close()


def wuzapi_user_id(db_path, name):
    """The wuzapi-internal `users.id` for a wuzapi user, looked up by name.

    This is NOT the account id from accounts.json and NOT the token: it is
    the random id wuzapi generates itself (`GenerateRandomID()`) when a
    user is created via `POST /admin/users`, and it is what wuzapi stamps
    into message_history.user_id on every row. wa-pair sets a wuzapi
    user's `name` to the account's `wuzapi_user` from accounts.json —
    NOT to the account id: the account migrated from a single-account
    install is `personal` but its wuzapi user is `omarchy`, the name that
    install already had. The code reads `wuzapi_user` back correctly; only
    this comment had drifted. Looking it up here by that same
    name is how a caller scopes count_history/wait_until_stable to one
    account. Returns None if the database, the `users` table, or a
    matching row does not exist yet — callers must treat that as "cannot
    scope" and fall back to an unscoped (whole-table) wait, never as
    "this account has zero history".
    """
    p = Path(db_path)
    if not p.exists():
        return None
    conn = sqlite3.connect(p)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if cur.fetchone() is None:
            return None
        row = conn.execute(
            "SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def wait_until_stable(db_path, stable_checks=3, interval=5.0, timeout=180.0,
                      sleep_fn=None, now_fn=None, user_id=None) -> tuple:
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
        user_id: scope every count to this wuzapi user (see count_history).
            `None` (the default) counts the whole shared table. Pairing a
            second account while a first is already live means the table
            keeps growing from the first account's ordinary traffic the
            whole time this function polls; left unscoped, that growth
            alone can stop the count from ever settling within `timeout`,
            or falsely satisfy `saw_rows` with rows that are not the
            account actually being paired. Pass the wuzapi id of the
            account being paired (wuzapi_user_id) to make this function
            see only that account's own rows.
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
        # Called with the bare db_path when user_id is None (the default),
        # not count_history(db_path, user_id=None): existing callers —
        # including tests — that monkeypatch count_history with a
        # single-argument stand-in must keep working unscoped.
        count = count_history(db_path) if user_id is None else count_history(db_path, user_id=user_id)
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
