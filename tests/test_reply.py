import importlib.util, os, sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.runner import run

# bin/wa-reply has no .py extension, so spec_from_file_location returns None.
# Name the loader explicitly instead.
_loader = SourceFileLoader("wa_reply", str(ROOT / "bin" / "wa-reply"))
wa = importlib.util.module_from_spec(importlib.util.spec_from_loader("wa_reply", _loader))
_loader.exec_module(wa)


def test_fences_are_stripped_from_the_draft():
    assert wa.clean_draft("```\nHola Ana\n```") == "Hola Ana"


def test_language_tagged_fences_are_stripped():
    assert wa.clean_draft("```text\nHola\n```") == "Hola"


def test_plain_draft_is_untouched():
    assert wa.clean_draft("  Hola Ana  ") == "Hola Ana"


def test_empty_draft_is_rejected_before_sending():
    try:
        wa.send("56911111111@s.whatsapp.net", "   ")
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("an empty draft must never be sendable")


def test_bare_number_recipient_is_rejected():
    # wuzapi resolves a bare id through a PN/LID lookup that can be ambiguous.
    try:
        wa.send("56911111111", "hola")
    except ValueError as exc:
        assert "jid" in str(exc).lower()
    else:
        raise AssertionError("a recipient without @ must be rejected")


def test_dry_run_does_not_call_the_api():
    called = []
    original, wa._post = wa._post, lambda *a, **k: called.append(a)
    previous = os.environ.get("WA_DRY_RUN")
    os.environ["WA_DRY_RUN"] = "1"
    try:
        out = wa.send("56911111111@s.whatsapp.net", "hola")
        assert out["dry_run"] is True
        assert called == [], "dry run must not reach the network"
    finally:
        wa._post = original
        if previous is None:
            os.environ.pop("WA_DRY_RUN", None)
        else:
            os.environ["WA_DRY_RUN"] = previous


def test_secrets_file_supplies_the_token_when_the_env_lacks_it():
    import tempfile
    previous_dir = os.environ.get("WA_DATA_DIR")
    previous_token = os.environ.pop("WA_TOKEN", None)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "secrets.env").write_text("WA_TOKEN=fromfile\n")
        os.environ["WA_DATA_DIR"] = tmp
        try:
            wa._load_secrets()
            assert os.environ["WA_TOKEN"] == "fromfile"
        finally:
            os.environ.pop("WA_TOKEN", None)
            if previous_token is not None:
                os.environ["WA_TOKEN"] = previous_token
            if previous_dir is None:
                os.environ.pop("WA_DATA_DIR", None)
            else:
                os.environ["WA_DATA_DIR"] = previous_dir


def test_prompt_carries_context_idea_and_agent_rules():
    p = wa.build_prompt("Tono: breve.", [{"from": "Ana", "text": "podes el martes?"}],
                        "decile que si, despues de las 4", "Ana")
    assert "Tono: breve." in p
    assert "podes el martes?" in p
    assert "despues de las 4" in p
    assert "Ana" in p


if __name__ == "__main__":
    run(dict(globals()))
