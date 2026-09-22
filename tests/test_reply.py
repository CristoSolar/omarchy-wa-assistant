import importlib.util, io, json, os, sys
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.runner import run, tmpdir
from sink import store

# bin/wa-reply has no .py extension, so spec_from_file_location returns None.
# Name the loader explicitly instead.
_loader = SourceFileLoader("wa_reply", str(ROOT / "bin" / "wa-reply"))
wa = importlib.util.module_from_spec(importlib.util.spec_from_loader("wa_reply", _loader))
_loader.exec_module(wa)


def _queue_entry(entry_id="1@s.whatsapp.net"):
    return {"id": entry_id, "jid": entry_id, "push_name": "Ana", "text": "hola",
            "count": 1, "ts": "2026-09-22T10:00:00", "status": "pending"}


def _run_cmd_send(d, entry_id, text="hola"):
    """Run cmd_send with WA_DATA_DIR pointed at d; return its one JSON line."""
    previous_dir = os.environ.get("WA_DATA_DIR")
    os.environ["WA_DATA_DIR"] = str(d)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            wa.cmd_send(SimpleNamespace(id=entry_id, text=text))
    finally:
        if previous_dir is None:
            os.environ.pop("WA_DATA_DIR", None)
        else:
            os.environ["WA_DATA_DIR"] = previous_dir
    return json.loads(buf.getvalue())


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


def test_dry_run_is_the_default_when_the_env_var_is_entirely_absent():
    # The panel's Process spawns this script with a bare environment: no
    # WA_DRY_RUN key at all, not even "1". An implementation that read
    # os.environ.get("WA_DRY_RUN", "0") would pass every other test here
    # and then send for real on first real use.
    called = []
    original, wa._post = wa._post, lambda *a, **k: called.append(a)
    previous = os.environ.pop("WA_DRY_RUN", None)
    try:
        assert "WA_DRY_RUN" not in os.environ
        out = wa.send("56911111111@s.whatsapp.net", "hola")
        assert out["dry_run"] is True
        assert called == [], "a bare environment must still default to dry-run"
    finally:
        wa._post = original
        if previous is not None:
            os.environ["WA_DRY_RUN"] = previous


def test_api_must_stay_on_loopback():
    # secrets.env is read for WA_TOKEN; if it could also move WA_API, a
    # compromised or misconfigured secrets file could redirect a real send
    # to an attacker-controlled host. Refuse anything but 127.0.0.1.
    original_api = wa.API
    wa.API = "http://evil.example.com:8080"
    try:
        wa.send("56911111111@s.whatsapp.net", "hola")
    except ValueError as exc:
        assert "127.0.0.1" in str(exc)
    else:
        raise AssertionError("a non-loopback API must be refused")
    finally:
        wa.API = original_api


def test_fenced_block_after_a_preamble_is_extracted():
    # A real `claude -p` reply often wraps the fence in commentary; the
    # backticks and the preamble must never reach the recipient.
    raw = "Claro, aca va:\n```\nHola Ana\n```"
    assert wa.clean_draft(raw) == "Hola Ana"


def test_draft_with_no_fence_at_all_is_unchanged():
    assert wa.clean_draft("Hola Ana, todo bien") == "Hola Ana, todo bien"


def test_a_successful_send_removes_the_queue_entry_and_reports_ok():
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)
        original, wa.send = wa.send, lambda jid, text: {"data": {"Details": "Sent"}}
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert out == {"ok": True, "dry_run": False}
        assert store.load_queue(d) == [], "a confirmed send must clear the queue entry"


def test_a_raised_send_leaves_the_entry_recoverable_as_failed():
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)

        def _boom(jid, text):
            raise RuntimeError("wuzapi said no")

        original, wa.send = wa.send, _boom
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert out["ok"] is False
        q = store.load_queue(d)
        assert len(q) == 1, "a failed send must never remove the entry"
        assert q[0]["status"] == "failed"


def test_an_unconfirmed_response_leaves_the_entry_recoverable_as_failed():
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)
        # A response shape that proves nothing — including the AttributeError
        # trap of a `data` key that is null rather than a dict.
        original, wa.send = wa.send, lambda jid, text: {"data": None}
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert out["ok"] is False
        q = store.load_queue(d)
        assert len(q) == 1, "an unconfirmed send must never remove the entry"
        assert q[0]["status"] == "failed"


def test_a_timeout_leaves_the_entry_as_unknown_not_failed():
    # The single most dangerous case: if the request timed out after wuzapi
    # already accepted it, marking this "failed" invites a retry that sends
    # the same message twice to a real person.
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)

        def _timeout(jid, text):
            raise wa.SendOutcomeUnknown("timed out waiting for wuzapi")

        original, wa.send = wa.send, _timeout
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert out == {"ok": False, "unknown": True,
                       "error": "timed out waiting for wuzapi"}
        q = store.load_queue(d)
        assert len(q) == 1, "an unknown outcome must never remove the entry"
        assert q[0]["status"] == "unknown"


def test_post_maps_a_timeout_to_send_outcome_unknown():
    # This is the mapping the whole double-send protection rests on: prove
    # _post itself turns a timeout into SendOutcomeUnknown, not just that
    # cmd_send knows what to do once it receives one. The stub replaces the
    # function object, so no socket is ever opened.
    original_urlopen = wa.urllib.request.urlopen

    def _timeout(*a, **k):
        raise TimeoutError("timed out")

    wa.urllib.request.urlopen = _timeout
    try:
        try:
            wa._post("http://127.0.0.1:8080/chat/send/text",
                     {"Phone": "x", "Body": "y"}, "tok")
        except wa.SendOutcomeUnknown:
            pass
        else:
            raise AssertionError("a timeout must be mapped to SendOutcomeUnknown")
    finally:
        wa.urllib.request.urlopen = original_urlopen


def test_post_does_not_map_an_http_error_to_send_outcome_unknown():
    # The server answering (even with an error status) is a definite
    # outcome, not an unknown one; only "no answer at all" is unknown.
    from email.message import Message
    original_urlopen = wa.urllib.request.urlopen

    def _refused(*a, **k):
        raise wa.urllib.error.HTTPError(
            "http://127.0.0.1:8080/chat/send/text", 500, "boom", Message(), None)

    wa.urllib.request.urlopen = _refused
    try:
        try:
            wa._post("http://127.0.0.1:8080/chat/send/text",
                     {"Phone": "x", "Body": "y"}, "tok")
        except wa.SendOutcomeUnknown:
            raise AssertionError(
                "a definite HTTP error response must not be treated as unknown")
        except wa.urllib.error.HTTPError:
            pass
    finally:
        wa.urllib.request.urlopen = original_urlopen


def test_cmd_send_with_a_stale_id_reports_an_error_without_raising():
    with tmpdir() as d:
        out = _run_cmd_send(d, "does-not-exist@s.whatsapp.net")
        assert out == {"ok": False,
                       "error": "no queue entry with id does-not-exist@s.whatsapp.net"}


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
