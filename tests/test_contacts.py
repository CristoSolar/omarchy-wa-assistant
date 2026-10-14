# tests/test_contacts.py
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink.contacts import display_name, our_ids

OUR = "56900000001@s.whatsapp.net"
LID = "237786636488820@lid"
PN = "56911111111@s.whatsapp.net"


def _wuzdata(d, contacts=(), lids=()):
    """users.db maps the wuzapi user to its device jid; main.db is
    whatsmeow's store, with contacts keyed by the bare account jid."""
    u = sqlite3.connect(d / "users.db")
    u.execute("CREATE TABLE users (id TEXT, name TEXT, jid TEXT)")
    u.execute("INSERT INTO users VALUES ('x', 'trabajo', ?)", ("56900000001:27@s.whatsapp.net",))
    u.commit(); u.close()
    m = sqlite3.connect(d / "main.db")
    m.execute("CREATE TABLE whatsmeow_contacts (our_jid TEXT, their_jid TEXT, first_name TEXT,"
              " full_name TEXT, push_name TEXT, business_name TEXT, redacted_phone TEXT)")
    m.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT)")
    for their, full, push, biz in contacts:
        m.execute("INSERT INTO whatsmeow_contacts VALUES (?, ?, '', ?, ?, ?, '')",
                  (OUR, their, full, push, biz))
    m.executemany("INSERT INTO whatsmeow_lid_map VALUES (?, ?)", lids)
    m.commit(); m.close()
    return d


def test_a_lid_chat_shows_the_address_book_name_of_its_phone_number():
    with tmpdir() as d:
        _wuzdata(d, contacts=[(PN, "Ana Pérez", "ana", "")], lids=[("237786636488820", "56911111111")])
        assert display_name(d, "trabajo", LID, LID) == "Ana Pérez"


def test_the_address_book_name_wins_over_the_name_they_chose():
    with tmpdir() as d:
        _wuzdata(d, contacts=[(PN, "Ana Pérez", "ana", "")])
        assert display_name(d, "trabajo", PN, "ana 🌸") == "Ana Pérez"


def test_not_in_the_address_book_falls_back_to_the_event_push_name():
    with tmpdir() as d:
        _wuzdata(d)
        assert display_name(d, "trabajo", PN, "Rodrigo") == "Rodrigo"


def test_no_name_anywhere_shows_the_phone_number():
    with tmpdir() as d:
        _wuzdata(d, lids=[("237786636488820", "56911111111")])
        assert display_name(d, "trabajo", LID, LID) == "+56911111111"


def test_a_stored_push_name_on_the_lid_is_used_when_nothing_better_exists():
    with tmpdir() as d:
        _wuzdata(d, contacts=[(LID, "", "ezoficio", "")])
        assert display_name(d, "trabajo", LID, LID) == "ezoficio"


def test_a_business_without_an_address_book_entry_shows_its_business_name():
    with tmpdir() as d:
        _wuzdata(d, contacts=[(PN, "", "", "Ferretería Sur")])
        assert display_name(d, "trabajo", PN, PN) == "Ferretería Sur"


def test_another_accounts_address_book_is_never_used():
    with tmpdir() as d:
        _wuzdata(d)
        m = sqlite3.connect(d / "main.db")
        m.execute("INSERT INTO whatsmeow_contacts VALUES ('56999999999@s.whatsapp.net', ?, '', 'Otro', '', '', '')", (PN,))
        m.commit(); m.close()
        assert display_name(d, "trabajo", PN, PN) == "+56911111111"


def test_a_missing_store_never_breaks_queueing():
    with tmpdir() as d:
        assert display_name(d, "trabajo", LID, "Ana") == "Ana"
        assert display_name(d, "trabajo", LID, LID) == LID


def test_our_ids_are_the_account_number_and_its_lid():
    with tmpdir() as d:
        _wuzdata(d, lids=[("999", "56900000001")])
        assert our_ids(d, "trabajo") == {OUR, "999@lid"}
        assert our_ids(d, "nadie") == set()


if __name__ == "__main__":
    run(dict(globals()))
