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


PERSONAL = {"id": "personal", "label": "Personal", "wuzapi_user": "omarchy",
            "agent": "respuesta.md", "quick_replies": ["Uno", "Dos"]}
TRABAJO = {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo",
           "agent": "respuesta-trabajo.md", "quick_replies": ["Cotizo mañana"]}

TOKENS = {"personal": "tok-personal", "trabajo": "tok-trabajo"}


def _queue_entry(jid="1@s.whatsapp.net", account="personal"):
    return {"id": store.entry_id(account, jid), "jid": jid, "account": account,
            "push_name": "Ana", "text": "hola", "count": 1,
            "ts": "2026-09-22T10:00:00", "status": "pending"}


def _prepare(d, accts=(PERSONAL,), tokens=TOKENS):
    """accounts.json and a throwaway secrets.env for a test state dir.

    The real layout keeps secrets.env OUTSIDE the sink-visible directory
    (see test_the_secret_is_not_looked_for_inside_the_sink_mounted_data_dir,
    which proves that resolution); here it is just a throwaway path fed to
    WA_SECRETS_FILE, and the tokens in it are fake.
    """
    (d / "accounts.json").write_text(json.dumps(list(accts), ensure_ascii=False))
    if tokens is not None:
        (d / "secrets.env").write_text(
            "".join(f"{wa.token_env_var(k)}={v}\n" for k, v in tokens.items()))
    return d


def _enqueue(d, entry):
    """Queue an entry, seeding the default single-account setup if needed."""
    if not (d / "accounts.json").exists():
        _prepare(d)
    return store.upsert_queue(d, entry)


class _reply_env:
    """Point wa-reply at a test state dir, with no inherited token in view.

    Restores the whole environment afterwards: _load_secrets() uses
    setdefault, so a WA_TOKEN_* left behind by an earlier test would mask
    exactly the missing-token case one of these tests exists to prove.
    """

    def __init__(self, d):
        self.d = Path(d)

    def __enter__(self):
        self._saved = dict(os.environ)
        for key in [k for k in os.environ if k.startswith("WA_TOKEN")]:
            del os.environ[key]
        os.environ["WA_DATA_DIR"] = str(self.d)
        os.environ["WA_SECRETS_FILE"] = str(self.d / "secrets.env")
        return self.d

    def __exit__(self, *a):
        os.environ.clear()
        os.environ.update(self._saved)
        return False


def _run_cmd_send(d, entry_id, text="hola", acknowledged=False):
    """Run cmd_send with WA_DATA_DIR pointed at d; return its one JSON line."""
    buf = io.StringIO()
    with _reply_env(d):
        with redirect_stdout(buf):
            wa.cmd_send(SimpleNamespace(id=entry_id, text=text,
                                        acknowledged=acknowledged))
    return json.loads(buf.getvalue())


def test_fences_are_stripped_from_the_draft():
    assert wa.clean_draft("```\nHola Ana\n```") == "Hola Ana"


def test_language_tagged_fences_are_stripped():
    assert wa.clean_draft("```text\nHola\n```") == "Hola"


def test_plain_draft_is_untouched():
    assert wa.clean_draft("  Hola Ana  ") == "Hola Ana"


def test_empty_draft_is_rejected_before_sending():
    try:
        wa.send("56911111111@s.whatsapp.net", "   ", "tok")
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("an empty draft must never be sendable")


def test_bare_number_recipient_is_rejected():
    # wuzapi resolves a bare id through a PN/LID lookup that can be ambiguous.
    try:
        wa.send("56911111111", "hola", "tok")
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
        out = wa.send("56911111111@s.whatsapp.net", "hola", "tok")
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
    #
    # This must prove the CODE's default, not whatever WA_DRY_RUN happens to
    # be set to in the machine's real secrets.env. send() calls
    # _load_secrets(), which reads store.secrets_path() and would otherwise
    # repopulate WA_DRY_RUN from the user's live file the instant it is
    # popped. The runner already points WA_SECRETS_FILE at a throwaway
    # location for every test; this test additionally points it at a
    # tmpdir of its own with no secrets.env at all, so it proves the
    # default even if run outside this runner.
    called = []
    original, wa._post = wa._post, lambda *a, **k: called.append(a)
    previous_dry_run = os.environ.pop("WA_DRY_RUN", None)
    previous_secrets_file = os.environ.get("WA_SECRETS_FILE")
    with tmpdir() as d:
        os.environ["WA_SECRETS_FILE"] = str(d / "no-such-secrets.env")
        try:
            assert "WA_DRY_RUN" not in os.environ
            out = wa.send("56911111111@s.whatsapp.net", "hola", "tok")
            assert out["dry_run"] is True
            assert called == [], "a bare environment must still default to dry-run"
        finally:
            wa._post = original
            if previous_dry_run is not None:
                os.environ["WA_DRY_RUN"] = previous_dry_run
            if previous_secrets_file is None:
                os.environ.pop("WA_SECRETS_FILE", None)
            else:
                os.environ["WA_SECRETS_FILE"] = previous_secrets_file


def test_api_must_stay_on_loopback():
    # secrets.env is read for WA_TOKEN; if it could also move WA_API, a
    # compromised or misconfigured secrets file could redirect a real send
    # to an attacker-controlled host. Refuse anything but 127.0.0.1.
    original_api = wa.API
    wa.API = "http://evil.example.com:8080"
    try:
        wa.send("56911111111@s.whatsapp.net", "hola", "tok")
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
        _enqueue(d, entry)
        original, wa.send = wa.send, lambda jid, text, token: {"data": {"Details": "Sent"}}
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert out == {"ok": True, "dry_run": False}
        assert store.load_queue(d) == [], "a confirmed send must clear the queue entry"


def test_a_bookkeeping_failure_after_a_confirmed_send_never_leaves_it_resendable():
    """I2: remove_queue raising AFTER the message went out.

    The message reached the recipient. The only thing that failed is the
    queue write. "failed" has no acknowledgement requirement, so it would
    re-enable both send paths in the panel for a message already
    delivered — one click and a real person gets it twice. The entry must
    be left in a status that a second send refuses without --acknowledged.
    """
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)

        def _boom(dd, eid):
            raise OSError("read-only file system")

        orig_send, wa.send = wa.send, lambda jid, text, token: {"data": {"Details": "Sent"}}
        orig_remove, store.remove_queue = store.remove_queue, _boom
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = orig_send
            store.remove_queue = orig_remove

        assert out["ok"] is False
        q = store.load_queue(d)
        assert len(q) == 1, "the failed removal must leave the entry where it was"
        assert q[0]["status"] != "failed", (
            "a delivered message must never be left in a resendable status")
        assert q[0]["status"] in wa.UNCONFIRMED_STATUSES, (
            "the status must be one a second send refuses without an ack")


def test_a_bookkeeping_failure_after_a_confirmed_send_refuses_a_plain_resend():
    """The end-to-end consequence of the status above: the retry is refused."""
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)

        def _boom(dd, eid):
            raise OSError("read-only file system")

        orig_send, wa.send = wa.send, lambda jid, text, token: {"data": {"Details": "Sent"}}
        orig_remove, store.remove_queue = store.remove_queue, _boom
        try:
            _run_cmd_send(d, entry["id"])
        finally:
            store.remove_queue = orig_remove

        sent = []
        wa.send = lambda jid, text, token: (sent.append(text)
                                            or {"data": {"Details": "Sent"}})
        try:
            retry = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = orig_send
        assert retry["ok"] is False and retry.get("needs_ack") is True
        assert sent == [], "nothing may reach the network on an unacknowledged retry"


def test_a_raised_send_leaves_the_entry_recoverable_as_failed():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)

        def _boom(jid, text, token):
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
        _enqueue(d, entry)
        # A response shape that proves nothing — including the AttributeError
        # trap of a `data` key that is null rather than a dict.
        original, wa.send = wa.send, lambda jid, text, token: {"data": None}
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
        _enqueue(d, entry)

        def _timeout(jid, text, token):
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
    previous_file = os.environ.get("WA_SECRETS_FILE")
    previous_token = os.environ.pop("WA_TOKEN", None)
    with tempfile.TemporaryDirectory() as tmp:
        secrets = Path(tmp) / "secrets.env"
        secrets.write_text("WA_TOKEN=fromfile\n")
        os.environ["WA_SECRETS_FILE"] = str(secrets)
        try:
            wa._load_secrets()
            assert os.environ["WA_TOKEN"] == "fromfile"
        finally:
            os.environ.pop("WA_TOKEN", None)
            if previous_token is not None:
                os.environ["WA_TOKEN"] = previous_token
            if previous_file is None:
                os.environ.pop("WA_SECRETS_FILE", None)
            else:
                os.environ["WA_SECRETS_FILE"] = previous_file


def test_the_secret_is_not_looked_for_inside_the_sink_mounted_data_dir():
    # I2: the sink container bind-mounts WA_DATA_DIR. If the token were
    # resolved from there, handing the sink its state would also hand it the
    # credential. The two paths must move independently.
    import tempfile
    previous_dir = os.environ.get("WA_DATA_DIR")
    previous_file = os.environ.pop("WA_SECRETS_FILE", None)
    previous_root = os.environ.get("WA_STATE_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        os.environ["WA_STATE_ROOT"] = str(root)
        os.environ["WA_DATA_DIR"] = str(root / "state")
        try:
            secret = store.secrets_path()
            assert secret == root / "secrets.env"
            assert str(root / "state") not in str(secret), \
                "secrets.env must not live inside the sink's bind mount"
        finally:
            if previous_file is not None:
                os.environ["WA_SECRETS_FILE"] = previous_file
            if previous_dir is None:
                os.environ.pop("WA_DATA_DIR", None)
            else:
                os.environ["WA_DATA_DIR"] = previous_dir
            if previous_root is None:
                os.environ.pop("WA_STATE_ROOT", None)
            else:
                os.environ["WA_STATE_ROOT"] = previous_root


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
        _enqueue(d, entry)
        original, wa.send = wa.send, lambda jid, text, token: {"dry_run": True,
                                                               "jid": jid,
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
        _enqueue(d, entry)
        # upsert_queue forces "pending" only on an update; this is an insert.
        store.set_status(d, entry["id"], "unknown")

        called = []
        original, wa.send = wa.send, lambda jid, text, token: called.append((jid, text))
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
        _enqueue(d, entry)
        store.set_status(d, entry["id"], "unknown")

        called = []

        def _ok(jid, text, token):
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
        _enqueue(d, entry)

        def _send_then_new_message(jid, text, token):
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
    buf = io.StringIO()
    with _reply_env(d):
        with redirect_stdout(buf):
            wa.cmd_draft(SimpleNamespace(id=entry_id, idea=idea))
    return json.loads(buf.getvalue())


def test_claude_exiting_clean_with_no_output_reports_an_error():
    # I5: an empty draft AND an empty error gave the panel a blank box with
    # no explanation of why.
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        original = wa.subprocess.run
        wa.subprocess.run = lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="   \n", stderr="")
        try:
            out = _run_cmd_draft(d, entry["id"])
        finally:
            wa.subprocess.run = original
        assert out["draft"] == ""
        assert out["error"], "an empty draft must come with an explanation"


def test_a_dry_run_does_not_erase_a_stored_unknown_guard():
    # Restoring a literal "pending" after a dry run permanently dropped the
    # C3 double-send guard for an entry nothing was ever sent for — and
    # dry-run is the default mode.
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        store.set_status(d, entry["id"], "unknown")
        original, wa.send = wa.send, lambda jid, text, token: {"dry_run": True}
        try:
            out = _run_cmd_send(d, entry["id"], acknowledged=True)
        finally:
            wa.send = original
        assert out == {"ok": True, "dry_run": True}
        q = store.load_queue(d)
        assert len(q) == 1
        assert q[0]["status"] == "unknown", \
            "a dry run sends nothing, so it must not clear the resend guard"

        # And the guard is still enforced on the next unacknowledged attempt.
        again = _run_cmd_send(d, entry["id"])
        assert again["needs_ack"] is True


def test_a_dry_run_leaves_a_failed_entry_failed():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        store.set_status(d, entry["id"], "failed")
        original, wa.send = wa.send, lambda jid, text, token: {"dry_run": True}
        try:
            _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
        assert store.load_queue(d)[0]["status"] == "failed"


def test_an_entry_stranded_at_sending_refuses_to_send_without_an_acknowledgement():
    # wa-reply writes "sending" before a POST that can take 20 seconds. If
    # it is killed mid-request (bar restart, logout), the entry stays there
    # and the message may well have reached WhatsApp — the same ambiguity
    # as "unknown", so the same guard.
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        store.set_status(d, entry["id"], "sending")

        called = []
        original, wa.send = wa.send, lambda jid, text, token: called.append((jid, text))
        try:
            out = _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original

        assert out["ok"] is False
        assert out["needs_ack"] is True
        assert out["status"] == "sending"
        assert "interrumpido" in out["error"]
        assert called == [], "a stranded 'sending' entry must not be re-sent blind"
        assert store.load_queue(d)[0]["status"] == "sending"


def test_an_entry_stranded_at_sending_sends_once_acknowledged():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        store.set_status(d, entry["id"], "sending")

        called = []

        def _ok(jid, text, token):
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


def _run_cmd_quick(d, entry_id):
    """Run cmd_quick with WA_DATA_DIR pointed at d; return its one JSON line."""
    buf = io.StringIO()
    with _reply_env(d):
        with redirect_stdout(buf):
            wa.cmd_quick(SimpleNamespace(id=entry_id))
    return json.loads(buf.getvalue())


def test_quick_prints_the_configured_list_of_the_entrys_account():
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        personal = _queue_entry(account="personal")
        trabajo = _queue_entry(account="trabajo")
        _enqueue(d, personal)
        _enqueue(d, trabajo)
        assert _run_cmd_quick(d, personal["id"]) == PERSONAL["quick_replies"]
        assert _run_cmd_quick(d, trabajo["id"]) == TRABAJO["quick_replies"]


def test_quick_prints_an_empty_list_when_the_account_has_none():
    with tmpdir() as d:
        _prepare(d, ({**PERSONAL, "quick_replies": []},))
        entry = _queue_entry()
        _enqueue(d, entry)
        assert _run_cmd_quick(d, entry["id"]) == []


def test_quick_prints_an_empty_list_when_the_key_is_absent():
    with tmpdir() as d:
        account = dict(PERSONAL)
        del account["quick_replies"]
        _prepare(d, (account,))
        entry = _queue_entry()
        _enqueue(d, entry)
        assert _run_cmd_quick(d, entry["id"]) == []


def test_quick_refuses_an_entry_whose_account_is_unknown():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        entry = _queue_entry(account="fantasma")
        _enqueue(d, entry)
        try:
            _run_cmd_quick(d, entry["id"])
            raise AssertionError("an unknown account must be refused, not guessed")
        except wa.UnknownAccount as exc:
            assert exc.payload["unknown_account"] == "fantasma"


def _run_cmd_accounts(d):
    """Run cmd_accounts with WA_DATA_DIR pointed at d; return its one JSON line."""
    buf = io.StringIO()
    with _reply_env(d):
        with redirect_stdout(buf):
            wa.cmd_accounts(SimpleNamespace())
    return json.loads(buf.getvalue())


def test_accounts_prints_id_and_label_for_every_configured_account():
    # Panel.qml labels each queue entry's `account` id (list badge, and the
    # "sale desde" line) by asking this, rather than the entry carrying a
    # denormalised copy of the label that a later rename could disagree
    # with.
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        assert _run_cmd_accounts(d) == [
            {"id": "personal", "label": "Personal"},
            {"id": "trabajo", "label": "Trabajo"},
        ]


def test_accounts_never_leaks_a_token_or_the_wuzapi_user_or_quick_replies():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        out = _run_cmd_accounts(d)
        assert out == [{"id": "personal", "label": "Personal"}]
        assert "wuzapi_user" not in out[0]
        assert "quick_replies" not in out[0]
        assert "agent" not in out[0]


def test_accounts_prints_an_empty_list_when_none_are_configured():
    # No accounts.json at all — a fresh or not-yet-migrated install —
    # reads as no accounts (accounts.load's own contract), not an error.
    with tmpdir() as d:
        with _reply_env(d):
            buf = io.StringIO()
            with redirect_stdout(buf):
                wa.cmd_accounts(SimpleNamespace())
            assert json.loads(buf.getvalue()) == []


def test_accounts_falls_back_to_the_id_when_a_label_is_missing():
    with tmpdir() as d:
        account = dict(PERSONAL)
        del account["label"]
        _prepare(d, (account,))
        assert _run_cmd_accounts(d) == [{"id": "personal", "label": "personal"}]


def test_quick_reports_a_store_read_error_the_same_way_other_subcommands_do():
    # cmd_list, cmd_draft and cmd_send never catch store.StoreReadError
    # themselves — it propagates out to main()'s handler, which prints the
    # `{"ok": False, "error": ...}` line and exits non-zero rather than
    # ever printing a bare `[]`. cmd_quick must behave identically: raise,
    # not swallow.
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        (d / "accounts.json").write_text("{not json")
        with _reply_env(d):
            try:
                wa.cmd_quick(SimpleNamespace(id=entry["id"]))
                raise AssertionError("expected StoreReadError for invalid JSON")
            except store.StoreReadError as exc:
                assert "accounts.json" in str(exc)


# --- Multi-account: the sending identity comes from the entry ----------


def test_the_token_variable_name_is_derived_in_exactly_one_place():
    # bin/wa-pair writes this variable and wa-reply reads it. The two must
    # agree, so there is one derivation and wa-pair reaches it (directly or
    # through the `token-var` subcommand) instead of rebuilding the name.
    assert wa.token_env_var("personal") == "WA_TOKEN_PERSONAL"
    assert wa.token_env_var("trabajo") == "WA_TOKEN_TRABAJO"
    buf = io.StringIO()
    with redirect_stdout(buf):
        wa.cmd_token_var(SimpleNamespace(account_id="trabajo"))
    assert buf.getvalue().strip() == "WA_TOKEN_TRABAJO"


def test_each_account_sends_with_its_own_token():
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        personal = _queue_entry(account="personal")
        trabajo = _queue_entry(account="trabajo")
        _enqueue(d, personal)
        _enqueue(d, trabajo)
        # The same contact wrote to both numbers: two entries, and each
        # answer must leave by the number it arrived on.
        assert personal["id"] != trabajo["id"]

        sent = []

        def _capture(jid, text, token):
            sent.append((jid, text, token))
            return {"data": {"Details": "Sent"}}

        original, wa.send = wa.send, _capture
        try:
            assert _run_cmd_send(d, personal["id"])["ok"] is True
            assert _run_cmd_send(d, trabajo["id"])["ok"] is True
        finally:
            wa.send = original

        assert [t for _, _, t in sent] == ["tok-personal", "tok-trabajo"]


def test_the_transport_receives_the_accounts_token_in_its_header():
    # The last link of the chain: whatever token cmd_send resolved is the
    # one that reaches the request. urlopen is stubbed, so no socket opens.
    seen = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data": {"Details": "Sent"}}'

    def _urlopen(req, *a, **k):
        seen.append(req.get_header("Token"))
        return _Resp()

    original_urlopen = wa.urllib.request.urlopen
    wa.urllib.request.urlopen = _urlopen
    try:
        wa._post("http://127.0.0.1:8080/chat/send/text",
                 {"Phone": "1@s.whatsapp.net", "Body": "hola"}, "tok-trabajo")
    finally:
        wa.urllib.request.urlopen = original_urlopen
    assert seen == ["tok-trabajo"]


def test_account_token_reads_each_accounts_own_variable():
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        with _reply_env(d):
            assert wa.account_token(PERSONAL) == "tok-personal"
            assert wa.account_token(TRABAJO) == "tok-trabajo"


def test_no_environment_variable_can_redirect_a_send_to_another_account():
    # The invariant: the account comes from the entry and from nothing
    # else. A WA_TOKEN (the pre-multi-account variable, still present in a
    # migrated secrets.env) or a stray WA_ACCOUNT must change nothing.
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        entry = _queue_entry(account="personal")
        _enqueue(d, entry)

        sent = []
        original, wa.send = wa.send, (
            lambda jid, text, token: sent.append(token)
            or {"data": {"Details": "Sent"}})
        previous = {k: os.environ.get(k) for k in ("WA_ACCOUNT", "WA_TOKEN")}
        os.environ["WA_ACCOUNT"] = "trabajo"
        os.environ["WA_TOKEN"] = "tok-trabajo"
        try:
            _run_cmd_send(d, entry["id"])
        finally:
            wa.send = original
            for k, v in previous.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        assert sent == ["tok-personal"]


def test_an_entry_whose_account_is_unknown_is_refused_and_never_posts():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        entry = _queue_entry(account="fantasma")
        _enqueue(d, entry)

        posted = []
        original_post, wa._post = wa._post, (
            lambda *a, **k: posted.append(a))
        try:
            _run_cmd_send(d, entry["id"])
            raise AssertionError(
                "an entry whose account is not configured must be refused")
        except wa.UnknownAccount as exc:
            assert exc.payload["unknown_account"] == "fantasma"
            assert exc.payload["ok"] is False
        finally:
            wa._post = original_post

        assert posted == [], "a refused entry must never reach the network"
        q = store.load_queue(d)
        assert len(q) == 1, "a refusal must not remove the entry"
        assert q[0]["status"] == "pending", \
            "a refused entry must not even be marked 'sending'"


def test_an_entry_with_no_account_field_at_all_is_refused():
    # A pre-migration entry that somehow survived: it names no account, so
    # there is no account to answer from. Refuse rather than assume one.
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        entry = _queue_entry()
        del entry["account"]
        _enqueue(d, entry)
        try:
            _run_cmd_send(d, entry["id"])
            raise AssertionError("an entry with no account must be refused")
        except wa.UnknownAccount:
            pass


def test_a_known_account_without_a_token_is_refused_distinctly():
    # Different problem, different fix: the accounts file is fine and the
    # credential is missing (pairing unfinished, or secrets.env rewritten).
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO), tokens={"personal": "tok-personal"})
        entry = _queue_entry(account="trabajo")
        _enqueue(d, entry)

        posted = []
        original_post, wa._post = wa._post, (lambda *a, **k: posted.append(a))
        try:
            _run_cmd_send(d, entry["id"])
            raise AssertionError("a missing token must be refused")
        except wa.MissingToken as exc:
            assert exc.payload["missing_token"] == "WA_TOKEN_TRABAJO"
            assert exc.payload["account"] == "trabajo"
            assert "unknown_account" not in exc.payload, \
                "a missing token is not an unknown account"
        finally:
            wa._post = original_post

        assert posted == []
        assert store.load_queue(d)[0]["status"] == "pending"


def test_the_two_refusals_are_different_types_with_different_messages():
    assert issubclass(wa.UnknownAccount, wa.ReplyRefused)
    assert issubclass(wa.MissingToken, wa.ReplyRefused)
    assert not issubclass(wa.MissingToken, wa.UnknownAccount)
    unknown = wa.UnknownAccount("fantasma").payload["error"]
    missing = wa.MissingToken("trabajo", "WA_TOKEN_TRABAJO").payload["error"]
    assert "accounts.json" in unknown and "secrets.env" not in unknown
    assert "secrets.env" in missing and "wa-pair" in missing


def _run_main(d, argv):
    """Run main() as the CLI does; return (exit code, parsed stdout line)."""
    buf = io.StringIO()
    previous_argv = sys.argv
    sys.argv = ["wa-reply"] + list(argv)
    code = 0
    with _reply_env(d):
        try:
            with redirect_stdout(buf):
                wa.main()
        except SystemExit as exc:
            code = exc.code
        finally:
            sys.argv = previous_argv
    return code, json.loads(buf.getvalue())


def test_a_refusal_exits_non_zero_with_one_json_line_on_stdout():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        entry = _queue_entry(account="fantasma")
        _enqueue(d, entry)
        posted = []
        original_post, wa._post = wa._post, (lambda *a, **k: posted.append(a))
        try:
            code, out = _run_main(d, ["send", entry["id"], "--text", "hola"])
        finally:
            wa._post = original_post
        assert code == 1, "a refusal must exit non-zero"
        assert out["ok"] is False and out["unknown_account"] == "fantasma"
        assert posted == []

        _prepare(d, (PERSONAL, TRABAJO), tokens={"personal": "tok-personal"})
        trabajo = _queue_entry(account="trabajo")
        _enqueue(d, trabajo)
        original_post, wa._post = wa._post, (lambda *a, **k: posted.append(a))
        try:
            code, out = _run_main(d, ["send", trabajo["id"], "--text", "hola"])
        finally:
            wa._post = original_post
        assert code == 1
        assert out["missing_token"] == "WA_TOKEN_TRABAJO"
        assert "unknown_account" not in out
        assert posted == []


def test_the_tone_file_is_the_accounts_own_not_a_fixed_one():
    with tmpdir() as d, tmpdir() as agents:
        _prepare(d, (PERSONAL, TRABAJO))
        (agents / "respuesta.md").write_text("Tono personal: relajado.")
        (agents / "respuesta-trabajo.md").write_text("Tono trabajo: formal.")
        personal = _queue_entry(account="personal")
        trabajo = _queue_entry(account="trabajo")
        _enqueue(d, personal)
        _enqueue(d, trabajo)

        prompts = []
        original_agents, wa.AGENTS_DIR = wa.AGENTS_DIR, agents
        original_run = wa.subprocess.run

        def _capture(cmd, *a, **k):
            prompts.append(cmd[-1])
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        wa.subprocess.run = _capture
        try:
            _run_cmd_draft(d, personal["id"])
            _run_cmd_draft(d, trabajo["id"])
        finally:
            wa.subprocess.run = original_run
            wa.AGENTS_DIR = original_agents

        assert "Tono personal: relajado." in prompts[0]
        assert "Tono trabajo: formal." not in prompts[0]
        assert "Tono trabajo: formal." in prompts[1]
        assert "Tono personal: relajado." not in prompts[1]


def test_the_tone_file_cannot_escape_the_agents_directory():
    account = {**PERSONAL, "agent": "../../etc/passwd"}
    assert wa.agent_path(account).parent == wa.AGENTS_DIR


def test_draft_reads_the_context_ring_of_the_entrys_account():
    with tmpdir() as d, tmpdir() as agents:
        _prepare(d, (PERSONAL, TRABAJO))
        jid = "1@s.whatsapp.net"
        store.append_context(d, jid, {"from": "Ana", "text": "cosa personal",
                                      "ts": "2026-09-22T10:00:00"}, "personal")
        store.append_context(d, jid, {"from": "Ana", "text": "cosa de trabajo",
                                      "ts": "2026-09-22T10:00:00"}, "trabajo")
        trabajo = _queue_entry(account="trabajo")
        _enqueue(d, trabajo)

        prompts = []
        original_agents, wa.AGENTS_DIR = wa.AGENTS_DIR, agents
        original_run = wa.subprocess.run
        wa.subprocess.run = lambda cmd, *a, **k: (
            prompts.append(cmd[-1])
            or SimpleNamespace(returncode=0, stdout="ok", stderr=""))
        try:
            _run_cmd_draft(d, trabajo["id"])
        finally:
            wa.subprocess.run = original_run
            wa.AGENTS_DIR = original_agents
        assert "cosa de trabajo" in prompts[0]
        assert "cosa personal" not in prompts[0]


def test_draft_refuses_an_entry_whose_account_is_unknown():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        entry = _queue_entry(account="fantasma")
        _enqueue(d, entry)
        original_run = wa.subprocess.run
        wa.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("claude must not run for an unknown account"))
        try:
            _run_cmd_draft(d, entry["id"])
            raise AssertionError("an unknown account must be refused")
        except wa.UnknownAccount:
            pass
        finally:
            wa.subprocess.run = original_run


def test_two_ids_differing_only_by_case_never_share_a_token():
    # The Critical: by_id matches exactly, so `trabajo` and `Trabajo` are
    # two DIFFERENT accounts, and an unvalidated upper-casing would give
    # them both WA_TOKEN_TRABAJO — a message out under the wrong identity
    # with no refusal. The charset makes the collision unrepresentable:
    # such a file does not load at all.
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO,
                     {"id": "Trabajo", "label": "Otra", "wuzapi_user": "otra"}))
        entry = _queue_entry(account="Trabajo")
        _enqueue(d, entry)

        posted = []
        original_post, wa._post = wa._post, (lambda *a, **k: posted.append(a))
        sent = []
        original_send, wa.send = wa.send, (
            lambda jid, text, token: sent.append(token))
        try:
            _run_cmd_send(d, entry["id"])
            raise AssertionError("a case-variant account id must never send")
        except store.StoreReadError as exc:
            assert "Trabajo" in str(exc)
        finally:
            wa._post = original_post
            wa.send = original_send
        assert sent == [] and posted == []
        assert store.load_queue(d)[0]["status"] == "pending"


def test_token_env_var_refuses_an_id_outside_the_charset():
    for bad in ("Trabajo", "mi-trabajo", "a b; echo PWNED", "", None):
        try:
            wa.token_env_var(bad)
            raise AssertionError(f"{bad!r} must not yield a token variable name")
        except ValueError:
            pass


def test_token_var_refuses_a_shell_unsafe_id_and_prints_nothing():
    # wa-pair substitutes this output into bash. It must be a legal shell
    # identifier or nothing at all.
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            wa.cmd_token_var(SimpleNamespace(account_id="a b; echo PWNED"))
        raise AssertionError("a shell-unsafe id must be refused")
    except SystemExit as exc:
        assert exc.code == 1
    assert buf.getvalue() == "", "nothing may reach stdout for a refused id"


def test_a_duplicate_account_id_is_refused_rather_than_first_wins():
    with tmpdir() as d:
        _prepare(d, (PERSONAL, {**PERSONAL, "quick_replies": ["Otra cosa"]}))
        entry = _queue_entry()
        _enqueue(d, entry)
        try:
            _run_cmd_quick(d, entry["id"])
            raise AssertionError("a duplicate id must be a loud error")
        except store.StoreReadError as exc:
            assert "duplicate" in str(exc)


def test_quick_on_a_vanished_entry_still_prints_an_array():
    # The panel binds this output to its quick-reply list; one shape in
    # every outcome. The non-zero exit carries the failure.
    with tmpdir() as d:
        _prepare(d)
        buf = io.StringIO()
        with _reply_env(d):
            try:
                with redirect_stdout(buf):
                    wa.cmd_quick(SimpleNamespace(id="personal:nope@s.whatsapp.net"))
                raise AssertionError("a vanished entry must exit non-zero")
            except SystemExit as exc:
                assert exc.code == 1
        assert json.loads(buf.getvalue()) == []


def test_an_entry_with_no_account_says_something_actionable():
    entry_id = "1@s.whatsapp.net"
    payload = wa.AccountNotStated(entry_id).payload
    assert payload["error"].count("None") == 0
    assert entry_id in payload["error"] and "wa-up" in payload["error"]
    assert isinstance(wa.AccountNotStated(None), wa.UnknownAccount)


# --- Settings screen: tone and quick-reply editing (Task 3) -------------


def _run_cmd(d, fn, **kwargs):
    """Run a wa-reply cmd_* function with WA_DATA_DIR pointed at d.

    Returns its one JSON line on success; re-raises a ReplyRefused (or any
    other exception) so callers can assert on the specific refusal type,
    the same pattern _run_cmd_quick's siblings already use for that.
    """
    buf = io.StringIO()
    with _reply_env(d):
        with redirect_stdout(buf):
            fn(SimpleNamespace(**kwargs))
    return json.loads(buf.getvalue())


def test_tone_round_trips_through_get_and_set():
    with tmpdir() as d, tmpdir() as agents:
        _prepare(d, (PERSONAL,))
        original_agents, wa.AGENTS_DIR = wa.AGENTS_DIR, agents
        try:
            got = _run_cmd(d, wa.cmd_tone_get, account_id="personal")
            assert got["ok"] is True
            assert got["text"] == "", "no tone file yet reads as empty, not an error"

            saved = _run_cmd(d, wa.cmd_tone_set, account_id="personal",
                             text="Tono: relajado y breve.", hash=got["hash"])
            assert saved["ok"] is True

            got2 = _run_cmd(d, wa.cmd_tone_get, account_id="personal")
            assert got2["text"] == "Tono: relajado y breve."
            assert got2["hash"] == saved["hash"]
        finally:
            wa.AGENTS_DIR = original_agents


def test_tone_set_refuses_a_concurrent_hand_edit_instead_of_clobbering_it():
    with tmpdir() as d, tmpdir() as agents:
        _prepare(d, (PERSONAL,))
        original_agents, wa.AGENTS_DIR = wa.AGENTS_DIR, agents
        try:
            got = _run_cmd(d, wa.cmd_tone_get, account_id="personal")
            # Someone edits the tone file by hand while the settings screen
            # still holds the copy read above.
            tone_path = wa.agent_path(PERSONAL)
            tone_path.write_text("Tono editado a mano mientras tanto.")

            try:
                _run_cmd(d, wa.cmd_tone_set, account_id="personal",
                        text="Tono desde la pantalla, ya viejo.", hash=got["hash"])
                raise AssertionError("a stale hash must be refused, not written")
            except wa.ToneConflict as exc:
                assert exc.payload["conflict"] is True
                assert exc.payload["account"] == "personal"
                assert "cambió en disco" in exc.payload["error"]

            # The hand edit survives untouched.
            assert tone_path.read_text() == "Tono editado a mano mientras tanto."
        finally:
            wa.AGENTS_DIR = original_agents


def test_tone_get_refuses_an_unknown_account():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        try:
            _run_cmd(d, wa.cmd_tone_get, account_id="fantasma")
            raise AssertionError("an unknown account must be refused")
        except wa.UnknownAccountId as exc:
            assert exc.payload["unknown_account"] == "fantasma"


def test_quick_replies_round_trip_through_get_and_set():
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        got = _run_cmd(d, wa.cmd_quick_get, account_id="personal")
        assert got["ok"] is True
        assert got["quick_replies"] == PERSONAL["quick_replies"]

        saved = _run_cmd(d, wa.cmd_quick_set, account_id="personal",
                         replies=json.dumps(["Nueva", "Lista"]), hash=got["hash"])
        assert saved["ok"] is True
        assert saved["quick_replies"] == ["Nueva", "Lista"]

        got2 = _run_cmd(d, wa.cmd_quick_get, account_id="personal")
        assert got2["quick_replies"] == ["Nueva", "Lista"]
        assert got2["hash"] == saved["hash"]

        # The other account is untouched by a write scoped to "personal".
        assert _run_cmd(d, wa.cmd_quick_get,
                        account_id="trabajo")["quick_replies"] == TRABAJO["quick_replies"]


def test_quick_set_refuses_a_concurrent_hand_edit_instead_of_clobbering_it():
    with tmpdir() as d:
        _prepare(d, (PERSONAL, TRABAJO))
        got = _run_cmd(d, wa.cmd_quick_get, account_id="personal")

        # Someone edits accounts.json by hand (a different account's field,
        # even) while the settings screen still holds the copy read above.
        accts_path = d / "accounts.json"
        accts = json.loads(accts_path.read_text())
        for a in accts:
            if a["id"] == "trabajo":
                a["label"] = "Editado a mano"
        accts_path.write_text(json.dumps(accts, ensure_ascii=False))

        try:
            _run_cmd(d, wa.cmd_quick_set, account_id="personal",
                    replies=json.dumps(["Se hubiera perdido"]), hash=got["hash"])
            raise AssertionError("a stale hash must be refused, not written")
        except wa.QuickRepliesConflict as exc:
            assert exc.payload["conflict"] is True
            assert exc.payload["account"] == "personal"

        # The hand edit survives untouched.
        current = {a["id"]: a for a in json.loads(accts_path.read_text())}
        assert current["trabajo"]["label"] == "Editado a mano"
        assert current["personal"]["quick_replies"] == PERSONAL["quick_replies"]


def test_quick_set_refuses_a_non_list_value():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        got = _run_cmd(d, wa.cmd_quick_get, account_id="personal")
        try:
            _run_cmd(d, wa.cmd_quick_set, account_id="personal",
                    replies=json.dumps("no es una lista"), hash=got["hash"])
            raise AssertionError("a non-list value must be refused")
        except wa.InvalidQuickReplies:
            pass
        assert json.loads((d / "accounts.json").read_text())[0]["quick_replies"] \
            == PERSONAL["quick_replies"], "a refused write must not touch the file"


def test_quick_set_refuses_a_list_with_an_empty_or_blank_element():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        got = _run_cmd(d, wa.cmd_quick_get, account_id="personal")
        for bad in (["Buena", ""], ["   "], ["Buena", 5]):
            try:
                _run_cmd(d, wa.cmd_quick_set, account_id="personal",
                        replies=json.dumps(bad), hash=got["hash"])
                raise AssertionError(f"{bad!r} must be refused")
            except wa.InvalidQuickReplies:
                pass
        assert json.loads((d / "accounts.json").read_text())[0]["quick_replies"] \
            == PERSONAL["quick_replies"], "a refused write must not touch the file"


def test_quick_set_refuses_an_unknown_account():
    with tmpdir() as d:
        _prepare(d, (PERSONAL,))
        current = _read_bytes(d / "accounts.json")
        try:
            _run_cmd(d, wa.cmd_quick_set, account_id="fantasma",
                    replies=json.dumps(["Hola"]), hash=wa._sha256_bytes(current))
            raise AssertionError("an unknown account must be refused")
        except wa.UnknownAccountId as exc:
            assert exc.payload["unknown_account"] == "fantasma"


def _read_bytes(path):
    return Path(path).read_bytes()


def test_tone_and_quick_settings_output_never_leaks_a_token_or_another_account():
    with tmpdir() as d, tmpdir() as agents:
        _prepare(d, (PERSONAL, TRABAJO))
        original_agents, wa.AGENTS_DIR = wa.AGENTS_DIR, agents
        try:
            tone = _run_cmd(d, wa.cmd_tone_get, account_id="personal")
            quick = _run_cmd(d, wa.cmd_quick_get, account_id="personal")
        finally:
            wa.AGENTS_DIR = original_agents

        for out in (tone, quick):
            assert set(out) <= {"ok", "account", "text", "hash", "quick_replies"}
            dumped = json.dumps(out)
            assert "tok-personal" not in dumped and "tok-trabajo" not in dumped
            assert "wuzapi_user" not in dumped and "omarchy" not in dumped
            assert "trabajo" not in dumped, \
                "a request scoped to 'personal' must not mention the other account"


# --- Task 1: the ring gains the outgoing half, marked by direction -----


def test_a_confirmed_send_appends_the_outgoing_text_to_the_ring():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        original, wa.send = wa.send, lambda jid, text, token: {"data": {"Details": "Sent"}}
        try:
            out = _run_cmd_send(d, entry["id"], text="dale, nos vemos")
        finally:
            wa.send = original
        assert out == {"ok": True, "dry_run": False}
        ring = store.load_context(d, entry["jid"], entry["account"])
        assert len(ring) == 1
        assert ring[0]["direction"] == "out"
        assert ring[0]["text"] == "dale, nos vemos"


def test_a_dry_run_writes_nothing_to_the_ring():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        original, wa.send = wa.send, lambda jid, text, token: {"dry_run": True,
                                                               "jid": jid,
                                                               "text": text}
        try:
            _run_cmd_send(d, entry["id"], text="no debería quedar")
        finally:
            wa.send = original
        assert store.load_context(d, entry["jid"], entry["account"]) == [], \
            "a dry run never touched the network, so the ring must stay empty"


def test_an_unknown_outcome_writes_nothing_to_the_ring():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)

        def _timeout(jid, text, token):
            raise wa.SendOutcomeUnknown("timed out waiting for wuzapi")

        original, wa.send = wa.send, _timeout
        try:
            _run_cmd_send(d, entry["id"], text="no se sabe si llegó")
        finally:
            wa.send = original
        assert store.load_context(d, entry["jid"], entry["account"]) == [], \
            "an unconfirmed outcome must never be recorded as a sent message"


def test_the_ring_cap_of_ten_holds_counting_both_directions():
    with tmpdir() as d:
        entry = _queue_entry()
        _enqueue(d, entry)
        original, wa.send = wa.send, lambda jid, text, token: {"data": {"Details": "Sent"}}
        try:
            for i in range(12):
                # A fresh inbound message keeps the entry present for the
                # next send (a confirmed send removes it otherwise).
                store.upsert_queue(d, {**entry, "text": f"in{i}",
                                       "ts": f"2026-09-22T10:{i:02d}:00"})
                store.append_context(d, entry["jid"], {"direction": "in",
                                     "from": "Ana", "text": f"in{i}"}, entry["account"])
                _run_cmd_send(d, entry["id"], text=f"out{i}")
        finally:
            wa.send = original
        ring = store.load_context(d, entry["jid"], entry["account"])
        assert len(ring) == 10, "the cap must hold across both directions combined"


def test_build_prompt_speaker_comes_from_the_direction_mark_not_the_name():
    # The near-miss this whole task exists to close: a contact called
    # exactly the same as the user (or the user's own second number
    # writing in) must never be read as "the user" from free text.
    context = [
        {"direction": "in", "from": "Cristo Solar", "text": "hola, soy yo"},
        {"direction": "out", "text": "hola! todo bien"},
    ]
    p = wa.build_prompt("tono", context, "idea", "Cristo Solar")
    assert "Cristo Solar: hola, soy yo" in p
    assert "Yo: hola! todo bien" in p
    assert "Cristo Solar: hola! todo bien" not in p, \
        "an outgoing message must never be labelled with the contact's name"


def test_build_prompt_renders_both_sides_distinguishably():
    context = [
        {"direction": "in", "from": "Ana", "text": "podes el martes?"},
        {"direction": "out", "text": "dale, nos vemos el martes"},
        {"direction": "in", "from": "Ana", "text": "genial, gracias"},
    ]
    p = wa.build_prompt("tono", context, "decile que confirme", "Ana")
    lines = p.splitlines()
    speakers = [l.split(":", 1)[0] for l in lines
                if l.startswith("Ana:") or l.startswith("Yo:")]
    assert speakers == ["Ana", "Yo", "Ana"], \
        "the three messages must render in order, each labelled by its own side"


def test_build_prompt_defaults_a_directionless_entry_to_inbound():
    # Pre-extract history (or a hand-built ring) may carry no "direction"
    # field at all; it must still render, on the inbound side, rather than
    # crash or silently disappear.
    context = [{"from": "Ana", "text": "podes el martes?"}]
    p = wa.build_prompt("tono", context, "idea", "Ana")
    assert "Ana: podes el martes?" in p


if __name__ == "__main__":
    run(dict(globals()))
