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


def _run_cmd_send(d, entry_id, text="hola", acknowledged=False):
    """Run cmd_send with WA_DATA_DIR pointed at d; return its one JSON line."""
    previous_dir = os.environ.get("WA_DATA_DIR")
    os.environ["WA_DATA_DIR"] = str(d)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            wa.cmd_send(SimpleNamespace(id=entry_id, text=text,
                                        acknowledged=acknowledged))
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


def test_a_dry_run_send_leaves_the_queue_entry_exactly_as_it_was():
    # C2: _is_confirmed_sent used to return True for {"dry_run": True}, so
    # cmd_send removed the entry and the panel refreshed it away. Dry-run is
    # the DEFAULT, so every first-run user lost their queue this way.
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)
        original, wa.send = wa.send, lambda jid, text: {"dry_run": True, "jid": jid,
                                                        "text": text}
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert out == {"ok": True, "dry_run": True}
        q = store.load_queue(d)
        assert len(q) == 1, "a dry run sends nothing, so it must remove nothing"
        assert q[0]["status"] == "pending", "a dry run must leave the entry pending"
        assert q[0]["text"] == entry["text"]
        assert q[0]["jid"] == entry["jid"]


def test_a_dry_run_never_confirms_a_send():
    assert wa._is_confirmed_sent({"dry_run": True}) is False


def test_an_unknown_entry_refuses_to_send_without_an_acknowledgement():
    # C3: the persisted status used to be write-only. A send that timed out
    # is recorded "unknown"; reopening the panel later showed a fresh-looking
    # pending entry with a live send button, and a real person got the same
    # message twice.
    with tmpdir() as d:
        entry = _queue_entry()
        entry["status"] = "unknown"
        store.upsert_queue(d, entry)
        # upsert_queue forces "pending" only on an update; this is an insert.
        store.set_status(d, entry["id"], "unknown")

        called = []
        original, wa.send = wa.send, lambda jid, text: called.append((jid, text))
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original

        assert out["ok"] is False
        assert out["needs_ack"] is True
        assert called == [], "an unacknowledged 'unknown' entry must not be sent"
        q = store.load_queue(d)
        assert len(q) == 1
        assert q[0]["status"] == "unknown", "the refusal must not clear the record"


def test_an_unknown_entry_sends_once_the_acknowledgement_flag_is_passed():
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)
        store.set_status(d, entry["id"], "unknown")

        called = []

        def _ok(jid, text):
            called.append((jid, text))
            return {"data": {"Details": "Sent"}}

        original, wa.send = wa.send, _ok
        try:
            out = _run_cmd_send(d, entry["id"], acknowledged=True)
        finally:
            wa.send = original

        assert out == {"ok": True, "dry_run": False}
        assert called == [(entry["jid"], "hola")]
        assert store.load_queue(d) == []


def test_a_message_that_arrived_mid_draft_survives_a_successful_send():
    # I3: removing the whole chat entry on success destroyed a message that
    # landed after the draft began, and the user never learned it existed.
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)

        def _send_then_new_message(jid, text):
            store.upsert_queue(d, {**entry, "text": "y otra cosa",
                                   "ts": "2026-09-22T10:05:00"})
            return {"data": {"Details": "Sent"}}

        original, wa.send = wa.send, _send_then_new_message
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original

        assert out["ok"] is True
        assert out["superseded"] is True
        q = store.load_queue(d)
        assert len(q) == 1, "a newer message must not be silently destroyed"
        assert q[0]["text"] == "y otra cosa"
        assert q[0]["status"] == "pending"


def _run_cmd_draft(d, entry_id, idea="decile que si"):
    previous_dir = os.environ.get("WA_DATA_DIR")
    os.environ["WA_DATA_DIR"] = str(d)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            wa.cmd_draft(SimpleNamespace(id=entry_id, idea=idea))
    finally:
        if previous_dir is None:
            os.environ.pop("WA_DATA_DIR", None)
        else:
            os.environ["WA_DATA_DIR"] = previous_dir
    return json.loads(buf.getvalue())


def test_claude_exiting_clean_with_no_output_reports_an_error():
    # I5: an empty draft AND an empty error gave the panel a blank box with
    # no explanation of why.
    with tmpdir() as d:
        entry = _queue_entry()
        store.upsert_queue(d, entry)
        original = wa.subprocess.run
        wa.subprocess.run = lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="   \n", stderr="")
        try:
            out = _run_cmd_draft(d, entry["id"])
        finally:
            wa.subprocess.run = original
        assert out["draft"] == ""
        assert out["error"], "an empty draft must come with an explanation"


if __name__ == "__main__":
    run(dict(globals()))
