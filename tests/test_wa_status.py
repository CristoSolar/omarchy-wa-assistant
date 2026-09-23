# tests/test_wa_status.py
"""Exercise bin/wa-status's account-aggregation logic end to end.

bin/wa-heal, bin/wa-pair and bin/wa-up are explicitly off-limits to run in
this environment (a live paired account with real pending messages), so
this file covers only bin/wa-status — the one account-aware script in
Task 4 that is safe to actually invoke: it never mutates state, never
sends anything, and every path below points at a throwaway tmpdir and a
local stand-in HTTP server, never wuzapi or ~/.local/share/omarchy-wa.

A stand-in server is used instead of mocking urllib.request.urlopen from
inside the running wa-status process, because wa-status's account
aggregation runs as a real subprocess (it is a bash script that shells
out to python3) — the only way to exercise "does the whole script
correctly aggregate N accounts' /session/status responses" is to give it
something real to call.
"""
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir

ROOT = Path(__file__).resolve().parents[1]
WA_STATUS = str(ROOT / "bin" / "wa-status")


class _FakeWuzapi(http.server.BaseHTTPRequestHandler):
    """Enough of wuzapi's HTTP surface for wa-status: /session/status."""

    # token -> "logged_in" | "not_logged_in" | (absent -> unknown token)
    TOKENS = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/session/status":
            self.send_response(404)
            self.end_headers()
            return
        token = self.headers.get("token", "")
        state = self.TOKENS.get(token)
        body = json.dumps({"data": {"loggedIn": state == "logged_in"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(tokens):
    _FakeWuzapi.TOKENS = dict(tokens)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _FakeWuzapi)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _run_wa_status(d, api, accounts=None, secrets_lines=(), queue=None):
    if accounts is not None:
        (d / "accounts.json").write_text(json.dumps(accounts, ensure_ascii=False))
    if queue is not None:
        (d / "queue.json").write_text(json.dumps(queue, ensure_ascii=False))
    secrets = d / "secrets.env"
    secrets.write_text("".join(f"{line}\n" for line in secrets_lines))
    env = {
        "WA_STATE_ROOT": str(d),
        "WA_DATA_DIR": str(d),
        "WA_SECRETS_FILE": str(secrets),
        "WA_API": api,
        "PATH": "/usr/bin:/bin",
    }
    out = subprocess.run([WA_STATUS], capture_output=True, text=True,
                         env=env, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_ok_true_only_when_every_configured_account_is_logged_in():
    httpd, port = _serve({"tok-a": "logged_in", "tok-b": "logged_in"})
    try:
        with tmpdir() as d:
            result = _run_wa_status(
                d, f"http://127.0.0.1:{port}",
                accounts=[{"id": "personal", "label": "Personal"},
                          {"id": "trabajo", "label": "Trabajo"}],
                secrets_lines=["WA_TOKEN_PERSONAL=tok-a", "WA_TOKEN_TRABAJO=tok-b"])
        assert result["ok"] is True
        assert result["logged_in"] is True
    finally:
        httpd.shutdown()


def test_ok_false_when_one_of_several_accounts_is_logged_out():
    """The review-focus requirement this task exists to satisfy."""
    httpd, port = _serve({"tok-a": "logged_in", "tok-b": "not_logged_in"})
    try:
        with tmpdir() as d:
            result = _run_wa_status(
                d, f"http://127.0.0.1:{port}",
                accounts=[{"id": "personal", "label": "Personal"},
                          {"id": "trabajo", "label": "Trabajo"}],
                secrets_lines=["WA_TOKEN_PERSONAL=tok-a", "WA_TOKEN_TRABAJO=tok-b"])
        assert result["ok"] is False, "must not report healthy with one account logged out"
        assert result["logged_in"] is False
        by_id = {a["id"]: a for a in result["accounts"]}
        assert by_id["personal"]["logged_in"] is True
        assert by_id["trabajo"]["logged_in"] is False
    finally:
        httpd.shutdown()


def test_ok_false_when_one_account_has_never_been_paired():
    httpd, port = _serve({"tok-a": "logged_in"})
    try:
        with tmpdir() as d:
            result = _run_wa_status(
                d, f"http://127.0.0.1:{port}",
                accounts=[{"id": "personal", "label": "Personal"},
                          {"id": "trabajo", "label": "Trabajo"}],
                secrets_lines=["WA_TOKEN_PERSONAL=tok-a"])  # trabajo never paired
        assert result["ok"] is False
        by_id = {a["id"]: a for a in result["accounts"]}
        assert by_id["trabajo"]["paired"] is False
        assert by_id["trabajo"]["logged_in"] is False
    finally:
        httpd.shutdown()


def test_pending_is_the_total_queue_length_across_every_accounts_entries():
    httpd, port = _serve({"tok-a": "logged_in"})
    try:
        with tmpdir() as d:
            queue = [
                {"id": "personal:1@s.whatsapp.net", "account": "personal"},
                {"id": "trabajo:1@s.whatsapp.net", "account": "trabajo"},
                {"id": "trabajo:2@s.whatsapp.net", "account": "trabajo"},
            ]
            result = _run_wa_status(
                d, f"http://127.0.0.1:{port}",
                accounts=[{"id": "personal", "label": "Personal"},
                          {"id": "trabajo", "label": "Trabajo"}],
                secrets_lines=["WA_TOKEN_PERSONAL=tok-a", "WA_TOKEN_TRABAJO=tok-a"],
                queue=queue)
        assert result["pending"] == 3
    finally:
        httpd.shutdown()


def test_zero_accounts_configured_falls_back_to_the_legacy_wa_token():
    """Pre-migration / brand-new: no accounts.json entries, only WA_TOKEN."""
    httpd, port = _serve({"tok-legacy": "logged_in"})
    try:
        with tmpdir() as d:
            result = _run_wa_status(
                d, f"http://127.0.0.1:{port}",
                accounts=[],
                secrets_lines=["WA_TOKEN=tok-legacy"])
        assert result["ok"] is True
        assert result["logged_in"] is True
        assert result["accounts"] == []
    finally:
        httpd.shutdown()


def test_unreachable_wuzapi_reports_ok_false():
    with tmpdir() as d:
        result = _run_wa_status(
            d, "http://127.0.0.1:1",  # nothing listens here
            accounts=[{"id": "personal", "label": "Personal"}],
            secrets_lines=["WA_TOKEN_PERSONAL=tok-a"])
    assert result["ok"] is False
    assert result["accounts"][0]["reachable"] is False


def test_a_corrupt_accounts_json_is_reported_not_swallowed():
    with tmpdir() as d:
        (d / "accounts.json").write_text("{not json")
        secrets = d / "secrets.env"
        secrets.write_text("")
        env = {"WA_STATE_ROOT": str(d), "WA_DATA_DIR": str(d),
               "WA_SECRETS_FILE": str(secrets), "WA_API": "http://127.0.0.1:1",
               "PATH": "/usr/bin:/bin"}
        out = subprocess.run([WA_STATUS], capture_output=True, text=True,
                             env=env, timeout=20)
        assert out.returncode == 0
        result = json.loads(out.stdout)
        assert result["ok"] is False
        assert "accounts.json" in out.stderr


if __name__ == "__main__":
    run(dict(globals()))
