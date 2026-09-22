"""Delete the WhatsApp history wuzapi stores on pairing.

The sync only fires when a device is paired, so this runs at startup and
again after pairing rather than on a timer.
"""
import sqlite3
from pathlib import Path


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
