import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink.purge import purge_history


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


if __name__ == "__main__":
    run(dict(globals()))
