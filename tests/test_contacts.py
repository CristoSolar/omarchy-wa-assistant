# tests/test_contacts.py
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink.contacts import display_name, muted_chats, our_ids, with_mention_names

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
        # whatsmeow's real key is the device jid, not the bare one users.db has.
        m.execute("INSERT INTO whatsmeow_contacts VALUES (?, ?, '', ?, ?, ?, '')",
                  ("56900000001:27@s.whatsapp.net", their, full, push, biz))
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



def test_muted_chats_are_the_ones_muted_on_the_phone_and_not_yet_expired():
    with tmpdir() as d:
        _wuzdata(d)
        m = sqlite3.connect(d / "main.db")
        m.execute("CREATE TABLE whatsmeow_chat_settings (our_jid TEXT, chat_jid TEXT,"
                  " muted_until BIGINT NOT NULL DEFAULT 0)")
        dev, other = "56900000001:27@s.whatsapp.net", "569000000012:3@s.whatsapp.net"
        m.executemany("INSERT INTO whatsmeow_chat_settings VALUES (?, ?, ?)", [
            (dev, "1@g.us", -1), (dev, "2@g.us", 2000), (dev, "3@g.us", 500),
            (dev, "4@g.us", 0), (other, "5@g.us", -1)])
        m.commit(); m.close()
        assert muted_chats(d, "trabajo", 1000) == {"1@g.us", "2@g.us"}
        assert muted_chats(d, "nadie", 1000) == set()



def test_mentions_show_names_instead_of_numbers():
    names = {"105@lid": "Felipe", "5691@s.whatsapp.net": "Ana"}.get
    assert with_mention_names("@105 y @5691: hola", ["105@lid", "5691:3@s.whatsapp.net".replace(":3", "")],
                              names) == "@Felipe y @Ana: hola"
    assert with_mention_names("sin nada", [], names) == "sin nada"
    assert with_mention_names("@999 no mencionado", ["105@lid"], names) == "@999 no mencionado"


if __name__ == "__main__":
    run(dict(globals()))
