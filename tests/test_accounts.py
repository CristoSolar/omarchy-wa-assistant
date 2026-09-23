import json, sys
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


if __name__ == "__main__":
    run(dict(globals()))
