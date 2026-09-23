# tests/test_unlink.py
"""Exercise bin/wa-unlink end to end, as a real subprocess.

bin/wa-pair, bin/wa-up and bin/wa-heal are off-limits to run in this
environment (a live paired account with real pending conversations), but
bin/wa-unlink is safe to actually invoke here: every test below points it
at a throwaway tmpdir (via WA_STATE_ROOT/WA_DATA_DIR/WA_SECRETS_FILE) and
either a local stand-in HTTP server or a closed port standing in for
wuzapi — never a real wuzapi, never anything reachable outside this
process.
"""
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import accounts, store

ROOT = Path(__file__).resolve().parents[1]
WA_UNLINK = str(ROOT / "bin" / "wa-unlink")

PERSONAL = {"id": "personal", "label": "Personal", "wuzapi_user": "omarchy",
            "agent": "respuesta.md", "quick_replies": ["Uno"]}
TRABAJO = {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo",
           "agent": "respuesta-trabajo.md", "quick_replies": ["Dos"]}


class _FakeWuzapi(http.server.BaseHTTPRequestHandler):
    """Enough of wuzapi's HTTP surface for wa-unlink: POST /session/logout."""

    seen_tokens = []

    def log_message(self, *a):
        pass

    # The wuzapi users this fake knows about, keyed by wuzapi-internal id.
    # wa-unlink looks a user up by NAME (the account entry's wuzapi_user,
    # which is "omarchy" for the migrated "personal" account) and deletes
    # it by that id.
    users = {}
    deleted_ids = []
    admin_tokens = []

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/session/logout":
            self.send_response(404)
            self.end_headers()
            return
        type(self).seen_tokens.append(self.headers.get("token", ""))
        self._json(200, {"data": {}})

    def do_GET(self):
        if self.path != "/admin/users":
            self.send_response(404)
            self.end_headers()
            return
        type(self).admin_tokens.append(self.headers.get("Authorization", ""))
        self._json(200, {"data": [{"id": uid, "name": name}
                                  for uid, name in type(self).users.items()]})

    def do_DELETE(self):
        prefix = "/admin/users/"
        if not self.path.startswith(prefix):
            self.send_response(404)
            self.end_headers()
            return
        uid = self.path[len(prefix):]
        if uid not in type(self).users:
            self.send_response(404)
            self.end_headers()
            return
        type(self).users.pop(uid)
        type(self).deleted_ids.append(uid)
        self._json(200, {"data": {}})


def _serve(users=None):
    _FakeWuzapi.seen_tokens = []
    _FakeWuzapi.users = dict(users or {})
    _FakeWuzapi.deleted_ids = []
    _FakeWuzapi.admin_tokens = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _FakeWuzapi)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _closed_port_api():
    """A URL nothing listens on: an HTTPServer bound then shut down,
    freeing the port so a connection to it fails fast rather than being
    accepted by a stale listener."""
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _FakeWuzapi)
    port = httpd.server_address[1]
    httpd.server_close()
    return f"http://127.0.0.1:{port}"


def _setup(d, accts, secrets_lines=(), queue=None, ctx=None):
    (d / "accounts.json").write_text(json.dumps(list(accts), ensure_ascii=False))
    (d / "secrets.env").write_text("".join(f"{ln}\n" for ln in secrets_lines))
    if queue is not None:
        (d / "queue.json").write_text(json.dumps(queue, ensure_ascii=False))
    if ctx:
        for account_id, jids in ctx.items():
            cdir = d / "ctx" / account_id
            cdir.mkdir(parents=True, exist_ok=True)
            for jid, ring in jids.items():
                (cdir / f"{jid}.json").write_text(json.dumps(ring, ensure_ascii=False))


def _entry(account, jid, eid=None):
    return {"id": eid or store.entry_id(account, jid), "jid": jid, "account": account,
            "push_name": "Ana", "text": "hola", "count": 1,
            "ts": "2026-09-22T10:00:00", "status": "pending"}


def _env(d, api, secrets=None):
    return {
        "WA_STATE_ROOT": str(d),
        "WA_DATA_DIR": str(d),
        "WA_SECRETS_FILE": str(secrets or (d / "secrets.env")),
        "WA_API": api,
        "PATH": "/usr/bin:/bin",
    }


def _run(args, d, api, secrets=None, timeout=15):
    return subprocess.run([WA_UNLINK, *args], capture_output=True, text=True,
                          env=_env(d, api, secrets), timeout=timeout)


def _both_accounts_queue():
    return [
        _entry("personal", "1@s.whatsapp.net"),
        _entry("personal", "2@s.whatsapp.net"),
        _entry("trabajo", "3@s.whatsapp.net"),
    ]


# --- Every trace of the account is gone, the other account is untouched ---

def test_full_unlink_removes_every_trace_and_leaves_the_other_account_intact():
    httpd, port = _serve()
    try:
        with tmpdir() as d:
            _setup(d, [PERSONAL, TRABAJO],
                   secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p",
                                  f"{accounts.token_env_var('trabajo')}=tok-t"],
                   queue=_both_accounts_queue(),
                   ctx={"personal": {"1_s.whatsapp.net": [{"from": "x", "text": "hi", "ts": "1"}]},
                        "trabajo": {"3_s.whatsapp.net": [{"from": "y", "text": "yo", "ts": "1"}]}})
            result = _run(["personal"], d, f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr

            accts = accounts.load(d)
            assert [a["id"] for a in accts] == ["trabajo"]

            secrets_text = (d / "secrets.env").read_text()
            assert accounts.token_env_var("personal") not in secrets_text
            assert "tok-t" in secrets_text  # trabajo's token untouched

            assert not (d / "ctx" / "personal").exists()
            assert (d / "ctx" / "trabajo").exists()
            assert (d / "ctx" / "trabajo" / "3_s.whatsapp.net.json").exists()

            queue = store.load_queue(d)
            assert [e["account"] for e in queue] == ["trabajo"]
            assert len(queue) == 1

            assert _FakeWuzapi.seen_tokens == ["tok-p"]
    finally:
        httpd.shutdown()


def test_reports_the_discarded_count_in_json_mode():
    httpd, port = _serve()
    try:
        with tmpdir() as d:
            _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
                   queue=[_entry("personal", "1@s.whatsapp.net"),
                          _entry("personal", "2@s.whatsapp.net")])
            result = _run(["--json", "personal"], d, f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr
            lines = [json.loads(ln) for ln in result.stdout.splitlines() if ln.strip()]
            events = {ln["event"]: ln for ln in lines}
            assert events["done"]["discarded"] == 2
            assert events["queue_discarded"]["rows"] == 2
    finally:
        httpd.shutdown()


def test_reports_the_discarded_count_in_human_mode():
    httpd, port = _serve()
    try:
        with tmpdir() as d:
            _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
                   queue=[_entry("personal", "1@s.whatsapp.net")])
            result = _run(["personal"], d, f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr
            assert "1" in result.stdout
    finally:
        httpd.shutdown()


# --- Unlinking a nonexistent account fails cleanly, changes nothing -------

def test_unlinking_an_unknown_account_fails_cleanly_and_changes_nothing():
    with tmpdir() as d:
        _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
               queue=[_entry("personal", "1@s.whatsapp.net")])
        before_accounts = accounts.load(d)
        before_secrets = (d / "secrets.env").read_text()
        before_queue = store.load_queue(d)
        result = _run(["nope"], d, "http://127.0.0.1:1")
        assert result.returncode != 0
        assert accounts.load(d) == before_accounts
        assert (d / "secrets.env").read_text() == before_secrets
        assert store.load_queue(d) == before_queue


def test_unlinking_an_invalid_account_id_fails_cleanly():
    with tmpdir() as d:
        _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"])
        result = _run(["Not-Valid!"], d, "http://127.0.0.1:1")
        assert result.returncode != 0
        assert accounts.load(d) == [PERSONAL]


# --- Re-run after a crash at each step converges ---------------------------

def test_rerun_after_accounts_json_already_removed_converges():
    """Simulates a crash between step 2 (accounts.json entry removed) and
    step 3 (token removed): the account is already gone from
    accounts.json, but the token, ctx ring and queue entries are still on
    disk. A re-run must finish the job without an account entry to key
    off, not refuse it as unknown."""
    with tmpdir() as d:
        _setup(d, [TRABAJO],  # "personal" already removed from accounts.json
               secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p",
                              f"{accounts.token_env_var('trabajo')}=tok-t"],
               queue=_both_accounts_queue(),
               ctx={"personal": {"1_s.whatsapp.net": [{"from": "x", "text": "hi", "ts": "1"}]}})
        result = _run(["personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        assert accounts.load(d) == [TRABAJO]
        assert accounts.token_env_var("personal") not in (d / "secrets.env").read_text()
        assert not (d / "ctx" / "personal").exists()
        queue = store.load_queue(d)
        assert [e["account"] for e in queue] == ["trabajo"]


def test_rerun_after_token_already_removed_converges():
    """Crash between step 3 (token removed) and step 4 (ctx removed)."""
    with tmpdir() as d:
        _setup(d, [TRABAJO],
               secrets_lines=[f"{accounts.token_env_var('trabajo')}=tok-t"],  # personal's already gone
               queue=[_entry("personal", "1@s.whatsapp.net"), _entry("trabajo", "3@s.whatsapp.net")],
               ctx={"personal": {"1_s.whatsapp.net": [{"from": "x", "text": "hi", "ts": "1"}]}})
        result = _run(["personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        assert not (d / "ctx" / "personal").exists()
        queue = store.load_queue(d)
        assert [e["account"] for e in queue] == ["trabajo"]


def test_rerun_after_ctx_already_removed_converges():
    """Crash between step 4 (ctx removed) and step 5 (queue discarded)."""
    with tmpdir() as d:
        _setup(d, [TRABAJO],
               secrets_lines=[f"{accounts.token_env_var('trabajo')}=tok-t"],
               queue=[_entry("personal", "1@s.whatsapp.net"), _entry("trabajo", "3@s.whatsapp.net")])
        result = _run(["personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        queue = store.load_queue(d)
        assert [e["account"] for e in queue] == ["trabajo"]


def test_rerun_after_full_unlink_refuses_cleanly_as_if_never_configured():
    """After everything has already been removed, a second run finds no
    trace anywhere and refuses exactly like an id that was never
    configured — at that point the two are the same state."""
    with tmpdir() as d:
        _setup(d, [TRABAJO], secrets_lines=[f"{accounts.token_env_var('trabajo')}=tok-t"],
               queue=[_entry("trabajo", "3@s.whatsapp.net")])
        result = _run(["personal"], d, "http://127.0.0.1:1")
        assert result.returncode != 0
        assert accounts.load(d) == [TRABAJO]


def test_unreachable_wuzapi_does_not_block_local_cleanup():
    """The one non-local, non-reversible step: if wuzapi cannot be
    reached, local cleanup must still converge rather than get stuck
    forever behind a container that is down."""
    with tmpdir() as d:
        closed = _closed_port_api()
        _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
               queue=[_entry("personal", "1@s.whatsapp.net")])
        result = _run(["personal"], d, closed)
        assert result.returncode == 0, result.stderr
        assert accounts.load(d) == []
        assert store.load_queue(d) == []
        # curl's -w '%{http_code}' already prints "000" on a connection
        # failure; a naive `|| echo "000"` fallback duplicates it into
        # "000000" in the message a person reads right when something is
        # already going wrong.
        assert "HTTP 000)" in result.stderr, result.stderr
        assert "000000" not in result.stderr


# --- Unlinking the last account leaves a valid empty state -----------------

def test_unlinking_the_last_account_leaves_a_valid_empty_state():
    httpd, port = _serve()
    try:
        with tmpdir() as d:
            _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
                   queue=[_entry("personal", "1@s.whatsapp.net")])
            result = _run(["personal"], d, f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr
            assert accounts.load(d) == []
            assert store.load_queue(d) == []
            # wa-status's own aggregation must handle an empty accounts.json
            # (no accounts configured) without raising.
            import subprocess as sp
            wa_status = str(ROOT / "bin" / "wa-status")
            status = sp.run([wa_status], capture_output=True, text=True,
                            env=_env(d, f"http://127.0.0.1:{port}"), timeout=15)
            payload = json.loads(status.stdout)
            assert payload["accounts"] == []
    finally:
        httpd.shutdown()


# --- The count-only query --------------------------------------------------

def test_count_only_returns_the_right_number_and_changes_nothing():
    with tmpdir() as d:
        _setup(d, [PERSONAL, TRABAJO],
               secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p",
                              f"{accounts.token_env_var('trabajo')}=tok-t"],
               queue=_both_accounts_queue())
        before_accounts = accounts.load(d)
        before_secrets = (d / "secrets.env").read_text()
        before_queue = store.load_queue(d)
        result = _run(["--count-only", "personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "2"
        assert accounts.load(d) == before_accounts
        assert (d / "secrets.env").read_text() == before_secrets
        assert store.load_queue(d) == before_queue


def test_count_only_json_mode():
    with tmpdir() as d:
        _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
               queue=[_entry("personal", "1@s.whatsapp.net")])
        result = _run(["--count-only", "--json", "personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        lines = [json.loads(ln) for ln in result.stdout.splitlines() if ln.strip()]
        events = {ln["event"]: ln for ln in lines}
        assert events["count"]["pending"] == 1
        assert events["count"]["account"] == "personal"


# --- A mutation step that FAILS must stop, not report success -------------
#
# The four local mutations used to run with their exit status unchecked
# under `set -uo pipefail` (no -e): a python3 that died printed a traceback
# to stderr — which the settings screen never reads — and the script emitted
# that step's success event anyway, then `done`, then exit 0. The UI turned
# that into "Cuenta desvinculada." over an account still sitting in
# accounts.json with its queue entry still pending. None of the tests here
# exercised a failing step, which is how it survived two reviews.

def _terminal_events(stdout):
    return [json.loads(ln) for ln in stdout.splitlines() if ln.strip()]


def test_a_failing_accounts_json_write_errors_out_and_changes_nothing_after_it():
    if __import__("os").geteuid() == 0:
        return  # root ignores the unwritable directory this test depends on
    with tmpdir() as d:
        _setup(d, [PERSONAL, TRABAJO],
               secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p",
                              f"{accounts.token_env_var('trabajo')}=tok-t"],
               queue=_both_accounts_queue(),
               ctx={"personal": {"1_s.whatsapp.net": [{"from": "x", "text": "hi", "ts": "1"}]}})
        # accounts.json is written through store.atomic_write, which creates
        # its temp file in the same directory: a read-only directory fails
        # that write while leaving every file in it readable, which is the
        # shape of both conditions this guards against (a stray permission,
        # and a full disk).
        d.chmod(0o500)
        try:
            result = _run(["--json", "personal"], d, "http://127.0.0.1:1")
        finally:
            d.chmod(0o700)

        assert result.returncode != 0, result.stdout
        events = _terminal_events(result.stdout)
        names = [e["event"] for e in events]
        assert "error" in names, names
        # The step that failed must NOT have announced itself, and nothing
        # after it may run or be announced.
        assert "account_removed" not in names, names
        assert "token_removed" not in names, names
        assert "context_removed" not in names, names
        assert "queue_discarded" not in names, names
        assert "done" not in names, names
        err = [e for e in events if e["event"] == "error"][-1]
        assert err.get("fatal") is True
        assert "accounts.json" in err["reason"]

        # And on disk: the account is untouched, not half-removed.
        assert [a["id"] for a in accounts.load(d)] == ["personal", "trabajo"]
        assert accounts.token_env_var("personal") in (d / "secrets.env").read_text()
        assert (d / "ctx" / "personal").exists()
        assert len(store.load_queue(d)) == 3


def test_a_failing_token_removal_stops_before_the_remaining_steps():
    if __import__("os").geteuid() == 0:
        return
    with tmpdir() as d:
        holder = d / "secrets"
        holder.mkdir()
        secrets = holder / "secrets.env"
        secrets.write_text(f"{accounts.token_env_var('personal')}=tok-p\n")
        _setup(d, [PERSONAL], queue=[_entry("personal", "1@s.whatsapp.net")],
               ctx={"personal": {"1_s.whatsapp.net": [{"from": "x", "text": "hi", "ts": "1"}]}})
        # The token is rewritten via a temp file NEXT TO secrets.env, so an
        # unwritable parent fails step 3 only — steps 1 and 2 (which live in
        # the writable data dir) succeed first, which is exactly the "it
        # already committed, then a later step died" case.
        holder.chmod(0o500)
        try:
            result = _run(["--json", "personal"], d, "http://127.0.0.1:1", secrets=secrets)
        finally:
            holder.chmod(0o700)

        assert result.returncode != 0, result.stdout
        names = [e["event"] for e in _terminal_events(result.stdout)]
        assert "account_removed" in names, names   # step 2 really did happen
        assert "token_removed" not in names, names  # step 3 failed: not claimed
        assert "context_removed" not in names, names
        assert "queue_discarded" not in names, names
        assert "done" not in names, names
        assert names[-1] == "error", names
        # The commit point stands (that is what makes a re-run converge),
        # and nothing past the failed step ran.
        assert accounts.load(d) == []
        assert "tok-p" in secrets.read_text()
        assert (d / "ctx" / "personal").exists()
        assert len(store.load_queue(d)) == 1


# --- The wuzapi user is removed, so a re-link of the same id works --------

def test_the_wuzapi_user_is_deleted_so_a_relink_does_not_collide():
    httpd, port = _serve(users={"uid-omarchy": "omarchy", "uid-trabajo": "trabajo"})
    try:
        with tmpdir() as d:
            _setup(d, [PERSONAL, TRABAJO],
                   secrets_lines=["WUZAPI_ADMIN_TOKEN=admin-tok",
                                  f"{accounts.token_env_var('personal')}=tok-p",
                                  f"{accounts.token_env_var('trabajo')}=tok-t"],
                   queue=[])
            result = _run(["--json", "personal"], d, f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr
            events = {e["event"]: e for e in _terminal_events(result.stdout)}
            assert events["user_removed"]["ok"] is True
            # Looked up by the account entry's wuzapi_user ("omarchy"),
            # never by the account id ("personal") — the migrated
            # single-account install is the case that tells them apart.
            assert events["user_removed"]["user"] == "omarchy"
            assert _FakeWuzapi.deleted_ids == ["uid-omarchy"]
            assert _FakeWuzapi.users == {"uid-trabajo": "trabajo"}
            assert _FakeWuzapi.admin_tokens == ["admin-tok"]
    finally:
        httpd.shutdown()


def test_a_wuzapi_user_that_cannot_be_removed_is_reported_not_swallowed():
    with tmpdir() as d:
        # No WUZAPI_ADMIN_TOKEN on file: the user cannot be deleted at all.
        # The unlink still completes locally (an unreachable or unusable
        # wuzapi must never make an account un-removable), but the leftover
        # user is what breaks the NEXT link, so it must be said out loud.
        _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
               queue=[])
        result = _run(["--json", "personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        events = {e["event"]: e for e in _terminal_events(result.stdout)}
        assert events["user_removed"]["ok"] is False
        assert "WUZAPI_ADMIN_TOKEN" in events["user_removed"]["reason"]
        assert events["done"] is not None
        assert accounts.load(d) == []


def test_the_leftover_user_warning_reaches_human_mode_too():
    with tmpdir() as d:
        _setup(d, [PERSONAL], secrets_lines=[f"{accounts.token_env_var('personal')}=tok-p"],
               queue=[])
        result = _run(["personal"], d, "http://127.0.0.1:1")
        assert result.returncode == 0, result.stderr
        assert "WUZAPI_ADMIN_TOKEN" in result.stderr
        assert "NOT removed" in result.stderr



if __name__ == "__main__":
    run(dict(globals()))
