import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import accounts, store


def _write_accounts(d, accts):
    (d / "accounts.json").write_text(json.dumps(accts, ensure_ascii=False))


def test_load_missing_file_reads_as_empty_list():
    with tmpdir() as d:
        assert accounts.load(d) == []


def test_load_reads_the_configured_accounts():
    with tmpdir() as d:
        accts = [{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"},
                 {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo"}]
        _write_accounts(d, accts)
        assert accounts.load(d) == accts


def test_load_corrupt_file_raises_instead_of_reading_as_empty():
    with tmpdir() as d:
        (d / "accounts.json").write_text("{not json")
        try:
            accounts.load(d)
            assert False, "expected StoreReadError for invalid JSON"
        except store.StoreReadError as exc:
            assert "accounts.json" in str(exc)


def test_by_instance_finds_the_matching_account():
    accts = [{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"},
             {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo"}]
    found = accounts.by_instance(accts, "trabajo")
    assert found is not None and found["id"] == "trabajo"


def test_by_instance_returns_none_for_an_unknown_instance():
    accts = [{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"}]
    assert accounts.by_instance(accts, "nope") is None


def test_by_id_finds_the_matching_account():
    accts = [{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"},
             {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo"}]
    found = accounts.by_id(accts, "trabajo")
    assert found is not None and found["wuzapi_user"] == "trabajo"


def test_by_id_returns_none_for_an_unknown_id():
    accts = [{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy"}]
    assert accounts.by_id(accts, "nope") is None


def test_validate_id_accepts_a_slug_and_refuses_anything_else():
    assert accounts.validate_id("personal") == "personal"
    assert accounts.validate_id("trabajo_2") == "trabajo_2"
    for bad in ("Trabajo", "mi-trabajo", "a b", "a b; echo PWNED", "", "café",
                "trabajo\n", None, 3):
        try:
            accounts.validate_id(bad)
            assert False, f"{bad!r} must be refused as an account id"
        except ValueError:
            pass


def test_load_refuses_an_id_that_is_not_a_slug():
    # The id is upper-cased to name that account's token variable, so an
    # id outside the charset can collide with another account's token.
    with tmpdir() as d:
        _write_accounts(d, [{"id": "Trabajo", "label": "T", "wuzapi_user": "t"}])
        try:
            accounts.load(d)
            assert False, "a malformed id must be refused at load"
        except store.StoreReadError as exc:
            assert "Trabajo" in str(exc)


def test_load_refuses_two_ids_that_differ_only_by_case():
    # Both would derive WA_TOKEN_TRABAJO: one account would send with the
    # other's token, under the wrong number, with nothing refusing it.
    with tmpdir() as d:
        _write_accounts(d, [
            {"id": "trabajo", "label": "T", "wuzapi_user": "t"},
            {"id": "Trabajo", "label": "T2", "wuzapi_user": "t2"}])
        try:
            accounts.load(d)
            assert False, "case-variant ids must never both load"
        except store.StoreReadError:
            pass


def test_load_refuses_a_duplicate_id_instead_of_taking_the_first():
    # First-wins would silently give one account the other's tone and
    # quick replies.
    with tmpdir() as d:
        _write_accounts(d, [
            {"id": "trabajo", "label": "T", "wuzapi_user": "t",
             "agent": "a.md"},
            {"id": "trabajo", "label": "T", "wuzapi_user": "t2",
             "agent": "b.md"}])
        try:
            accounts.load(d)
            assert False, "a duplicate id must be a loud error"
        except store.StoreReadError as exc:
            assert "duplicate" in str(exc)


def test_load_refuses_an_account_that_is_not_an_object():
    with tmpdir() as d:
        _write_accounts(d, ["personal"])
        try:
            accounts.load(d)
            assert False, "a non-object account must be refused"
        except store.StoreReadError:
            pass


def test_the_migrations_own_account_id_passes_the_charset():
    # store.migrate_single_account creates the id "personal"; tightening
    # the charset must not make the migrated live install unloadable.
    with tmpdir() as d:
        state = d / "state"
        state.mkdir()
        (state / "queue.json").write_text(json.dumps(
            [{"id": "1@s.whatsapp.net", "jid": "1@s.whatsapp.net"}]))
        previous = {k: os.environ.get(k) for k in ("WA_DATA_DIR", "WA_SECRETS_FILE")}
        os.environ["WA_DATA_DIR"] = str(state)
        os.environ["WA_SECRETS_FILE"] = str(d / "secrets.env")
        try:
            assert store.migrate_single_account(d) is True
            loaded = accounts.load(state)
        finally:
            for k, v in previous.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        assert [a["id"] for a in loaded] == ["personal"]
        assert accounts.validate_id("personal") == "personal"


if __name__ == "__main__":
    run(dict(globals()))
