"""Procedure tests: the upgrade path and the panel's send gates.

These assert on the SOURCE of bin/wa-up, Panel.qml and install.sh rather
than on behaviour, because the properties at stake are an ordering inside
a bash script and a QML binding — neither is reachable from a Python
process, and the boundary for this branch forbids running wa-up, touching
Docker or starting a Quickshell panel at all. A source assertion is a
weaker test than an executed one, so each of these is written to fail
against the code as it was before the fix (verified by running them
against the previous commit), never as a restatement of whatever the file
happens to say.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.runner import run

WA_UP = (ROOT / "bin" / "wa-up").read_text()
WA_PAIR = (ROOT / "bin" / "wa-pair").read_text()
PANEL = (ROOT / "Panel.qml").read_text()
INSTALL = (ROOT / "install.sh").read_text()


# --- C1: the running sink can never be older than the code ----------------

def test_the_sink_image_is_rebuilt_unconditionally():
    """No `docker image inspect omarchy-wa-sink` guard around the build.

    With the guard, an install whose image predates a code change keeps
    running that image forever — including through the multi-account
    migration, which would then hand new-layout state to a sink that
    writes the old layout.
    """
    assert "docker image inspect omarchy-wa-sink" not in WA_UP, (
        "the sink build must not be guarded by an image-presence check")
    builds = [ln for ln in WA_UP.splitlines()
              if ln.startswith("docker build -t omarchy-wa-sink")]
    assert len(builds) == 1, (
        f"expected exactly one top-level, unindented sink build; got {builds}")


def test_the_wuzapi_image_is_still_only_checked_never_built_by_wa_up():
    """The third-party image keeps its presence check — only the sink changed."""
    assert "docker image inspect omarchy-wuzapi" in WA_UP
    assert not [ln for ln in WA_UP.splitlines()
                if ln.strip().startswith("docker build -t omarchy-wuzapi")], (
        "wa-up may only PRINT the wuzapi build command, never run it")


# --- C2: nothing is running while the state changes shape -----------------

def _idx(needle, text=WA_UP):
    i = text.find(needle)
    assert i != -1, f"not found in the script: {needle!r}"
    return i


def test_the_containers_are_torn_down_before_the_migration_runs():
    """A live sink writing old-shape events into just-migrated state is
    permanently unmigrated: the migration is gated on accounts.json, which
    by then exists, so it never runs again for that message."""
    assert _idx("docker rm -f omarchy-wuzapi omarchy-wa-sink") < \
        _idx("store.migrate_single_account("), (
            "docker rm -f must come BEFORE migrate_single_account")


def test_the_migration_runs_before_anything_is_started_again():
    assert _idx("store.migrate_single_account(") < \
        _idx("docker run -d --name omarchy-wa-sink"), (
            "the migration must finish before the new sink starts")


def test_both_images_are_resolved_before_the_stack_is_torn_down():
    """A build failure must leave the existing stack running."""
    teardown = _idx("docker rm -f omarchy-wuzapi omarchy-wa-sink")
    assert _idx("docker build -t omarchy-wa-sink") < teardown
    assert _idx("docker image inspect omarchy-wuzapi") < teardown


def test_the_ordering_is_documented_where_the_code_depends_on_it():
    """The ordering is invisible locally, so it drifts back unless the
    reason sits next to the line that carries it."""
    teardown = _idx("docker rm -f omarchy-wuzapi omarchy-wa-sink")
    preceding = WA_UP[:teardown].rsplit("\n\n", 1)[-1]
    assert "ORDERING" in preceding, (
        "the docker rm -f must be preceded by the note explaining why it "
        "cannot move below the migration")


# --- I1: quick replies obey every gate "Enviar" obeys ---------------------

_GATES = ("sendBlocked", "inFlight", "drafting", "needsAck")


def _enabled_after(marker):
    tail = PANEL[_idx(marker, PANEL):]
    m = re.search(r"enabled:(.*?)onClicked:", tail, re.S)
    assert m, f"no enabled/onClicked pair after {marker!r}"
    return m.group(1)


def test_a_quick_reply_button_is_disabled_while_a_send_is_unconfirmed():
    """sendBlocked is set only when a send's response could not be parsed —
    the one state where nothing on disk says whether the message arrived.
    A quick reply sends immediately, with no confirmation step, so leaving
    it out here was a one-click double send to a real person."""
    expr = _enabled_after("model: root.quickReplies")
    for gate in _GATES:
        assert gate in expr, (
            f"the quick-reply button's enabled expression must check {gate}: {expr!r}")


def test_the_quick_reply_gate_matches_the_enviar_gate():
    quick = _enabled_after("model: root.quickReplies")
    enviar = _enabled_after('text: "Enviar"')
    for gate in _GATES:
        assert (gate in quick) == (gate in enviar), (
            f"{gate} is checked by one send path and not the other")


def test_the_comment_above_the_quick_replies_names_sendblocked():
    """The previous comment asserted the buttons enforced the same guard as
    Enviar while the code did not. A comment claiming a safety property the
    code lacks is how this survived two reviews."""
    before = PANEL[:_idx("model: root.quickReplies", PANEL)]
    block = before[before.rindex("// One-click quick replies"):]
    assert "sendBlocked" in block


# --- C3: pairing state comes from wuzapi, not a marker file ---------------

def test_wa_up_does_not_gate_the_heal_decision_on_a_marker_file():
    """A real installation was paired before $STATE/.paired existed: no
    marker, but wuzapi has held a real session the whole time. Gating the
    whole self-heal block on the marker's existence made wa-up say
    'no account paired yet' about an account wa-status reported as
    logged in seconds later — leaving the webhook unre-asserted and every
    incoming message silently dropped."""
    assert '-f "$STATE/.paired"' not in WA_UP, (
        "wa-up must not gate on whether a $STATE/.paired marker file exists")
    assert "PAIRED_MARKER" not in WA_UP, (
        "wa-up must not carry a variable for a marker path it no longer reads")


def test_wa_pair_no_longer_writes_the_retired_marker():
    assert 'touch "$STATE/.paired"' not in WA_PAIR, (
        "wa-pair must not write a marker nothing reads any more")


def test_wa_up_asks_wuzapi_for_the_persisted_jid_per_account():
    """The real, per-account fact this decision needs: wuzapi persists a
    session's jid in its own DB the first time a login ever succeeds, and
    keeps echoing it back across a container restart even while loggedIn
    is transiently false during reconnect. Empty jid is what an honestly
    never-paired account looks like; that must be read from wuzapi, not
    inferred from a local file."""
    assert "data.get('jid')" in WA_UP or 'data.get("jid")' in WA_UP, (
        "wa-up must derive ever-paired-or-not from wuzapi's own jid field")


def test_the_never_paired_message_is_still_calm_and_per_account():
    assert "not paired yet — nothing to wait for." in WA_UP


def test_the_stuck_reconnect_message_is_still_loud_and_actionable():
    for phrase in ("DROPPED", "wa-heal timer will retry", "bin/wa-heal $account_id"):
        assert phrase in WA_UP, f"missing from the loud reconnect warning: {phrase!r}"


# --- I3: install.sh prints a command that exists --------------------------

def test_install_prints_wa_pair_with_an_account_id():
    for line in INSTALL.splitlines():
        if "wa-pair" in line and "echo" in line:
            assert re.search(r"wa-pair\s+\S+\s+<", line), (
                f"the one-argument wa-pair form no longer exists: {line!r}")


# --- Task 1: `wa-pair --json` ----------------------------------------------
#
# `bin/wa-pair` cannot be run for these tests — a live installation has a
# real paired number and real pending conversations (see the plan's Global
# Constraints). Everything below either asserts on the SOURCE of wa-pair, or
# executes the small, pure, side-effect-free python fragment that builds a
# JSON line (extracted from the source), never the bash script itself.

def _extract_echo_literals(text):
    """Every `echo "..."` string literal, in source order, honouring
    backslash escapes char-by-char (a regex with a `\\.` alternation gets
    this wrong on the webhook-retry block, which nests an escaped `\\"`
    right before a line-ending quote)."""
    out = []
    i = 0
    n = len(text)
    while True:
        idx = text.find('echo "', i)
        if idx == -1:
            break
        j = idx + len('echo "')
        buf = []
        while j < n:
            c = text[j]
            if c == "\\" and j + 1 < n:
                buf.append(c)
                buf.append(text[j + 1])
                j += 2
                continue
            if c == '"':
                break
            buf.append(c)
            j += 1
        out.append("".join(buf))
        i = j + 1
    return out


# The exact, in-order sequence of human-facing `echo "..."` string literals
# from bin/wa-pair BEFORE this task (frozen from `git show HEAD:bin/wa-pair`
# at the point this task started). Task 1 must not add, remove, reorder or
# reword a single one of these — only decide, per line, whether it still
# prints in --json mode.
EXPECTED_HUMAN_ECHOES = [
    "error: $SECRETS does not exist yet. Run wa-up first.",
    "error: could not resolve or create accounts.json entry for $ACCOUNT_ID:",
    "  $RESOLVE_OUT",
    "accounts.json: $ACCOUNT_ID ($ACCOUNTS_STATUS), wuzapi user: $WUZAPI_USER",
    "seeded tone file: $AGENT_FILE (edit this to change how $ACCOUNT_ID replies)",
    "$RESP",
    "$RESP",
    "error: failed to create wuzapi user (HTTP $HTTP_CODE): $BODY",
    "Linking code: $CODE",
    "WhatsApp > Linked devices > Link device > Link with phone number",
    "linked.",
    "error: webhook did not stick after login. GET /webhook shows:",
    "  ${GOT:-<unreadable>}",
    "  expected: $WEBHOOK_URL",
    "  Messages will NOT be delivered until this is fixed. Try:",
    "    curl -s -X POST $API/webhook -H 'token: \\$$TOKEN_VAR' \\\\",
    "      -H 'Content-Type: application/json' \\\\",
    "      -d '{\\\"webhookurl\\\":\\\"$WEBHOOK_URL\\\",\\\"events\\\":[\\\"Message\\\"]}'",
    "  then confirm with: curl -s $API/webhook -H 'token: \\$$TOKEN_VAR'",
    "webhook confirmed: $WEBHOOK_URL",
    "waiting for WhatsApp to finish sending history...",
    "error: could not read wuzapi's history table while waiting for the",
    "  history dump. NOTHING was purged and nothing was verified.",
    "  Check 'docker logs omarchy-wa-sink' and the database at:",
    "    $DB",
    "error: unexpected output from the history wait: ${WAIT_OUT@Q}",
    "error: could not re-count wuzapi's history table after the purge.",
    "error: could not re-count wuzapi's history table after the purge.",
    "history: no rows for $ACCOUNT_ID were ever observed in wuzapi's",
    "  message_history table. That may mean this account has no",
    "  history to sync, or it may mean WhatsApp had not started",
    "  sending it yet. This run cannot tell which. Nothing was",
    "  confirmed purged for this account, because nothing was ever seen.",
    "  If you want certainty, wait a few minutes and then run:",
    "    docker restart omarchy-wa-sink",
    "    docker logs omarchy-wa-sink   # look for the 'purged N history rows' line",
    "history purged: $PRE_PURGE row(s) seen for $ACCOUNT_ID, table now empty.",
    "WARNING: history may NOT have been fully cleared.",
    "  History was still arriving when purge ran: settled=$SETTLED",
    "  Rows seen for $ACCOUNT_ID before restart: $PRE_PURGE, rows remaining (all accounts): $POST_PURGE",
    "  Please run 'docker restart omarchy-wa-sink' in a minute and check again.",
    "timed out waiting for the link",
]


def test_wa_pair_human_mode_echo_text_is_byte_identical():
    """Every `echo "..."` literal, in order, is exactly what it was before
    --json existed. Verified to fail against the pre-task script by
    comparing this same extraction against `git show HEAD:bin/wa-pair`
    before this task's commit — the two were identical then, so this test
    only checks Task 1 did not also reword anything while adding the new
    mode."""
    assert _extract_echo_literals(WA_PAIR) == EXPECTED_HUMAN_ECHOES


def test_wa_pair_json_flag_is_stripped_before_positional_args_are_parsed():
    """--json must be additive: it can appear anywhere in argv, and the
    two positional args (account id, number) must parse exactly as before
    once it is removed, so the existing usage error is untouched."""
    assert '"$_arg" == "--json"' in WA_PAIR
    idx_strip = WA_PAIR.index('"$_arg" == "--json"')
    idx_account = WA_PAIR.index('ACCOUNT_ID="${1:?usage: wa-pair <account-id>')
    assert idx_strip < idx_account, (
        "--json must be stripped from argv before $1/$2 are read as "
        "account id / number")
    # The usage message itself is untouched — same text a missing-argument
    # invocation always printed.
    assert 'usage: wa-pair <account-id> <number, international, no +>' in WA_PAIR


def test_wa_pair_json_vocabulary_matches_the_spec_exactly():
    """Only these seven event names may appear as an `event=` literal —
    the spec's whole point is that the GUI parses a fixed vocabulary, not
    prose that gets reworded later."""
    code_lines = "\n".join(ln for ln in WA_PAIR.splitlines()
                            if not ln.strip().startswith("#"))
    emitted = set(re.findall(r'emit_(?:json|terminal) (\w+)', code_lines))
    assert emitted == {
        "validating", "code", "linked", "history_waiting",
        "history_purged", "done", "error",
    }, emitted


def test_every_wa_pair_exit_statement_emits_a_terminal_event_first():
    """The property this task exists to guarantee: every `exit N` bash can
    reach on purpose is preceded, within a few lines, by an emit_terminal
    call. Fails against the pre-task script, which has no emit_terminal at
    all, so every one of these `exit` statements would be reported as a
    violation."""
    lines = WA_PAIR.splitlines()
    checked = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ("exit 0", "exit 1"):
            window = "\n".join(lines[max(0, i - 4):i])
            assert "emit_terminal" in window, (
                f"line {i + 1} ({stripped!r}) has no emit_terminal call in "
                f"the 4 lines before it:\n{window}")
            checked += 1
    # Sanity: the script still has the exit statements this counts — a
    # rewrite that removed them all would make the loop above vacuously
    # pass.
    assert checked >= 12, f"expected at least 12 bare exit statements, found {checked}"


def test_wa_pair_exit_trap_is_the_backstop_for_set_dash_e():
    """set -euo pipefail can abort the script at a curl or python failure
    this task did not explicitly annotate (an unchecked pipeline, a
    missing-argument usage error before $1/$2 are even read). The EXIT
    trap is what keeps THAT silent too — it must be registered before the
    positional args are parsed, and it must emit whichever of
    done/error a JSON run left out."""
    assert "trap on_exit EXIT" in WA_PAIR
    idx_trap = WA_PAIR.index("trap on_exit EXIT")
    idx_account = WA_PAIR.index('ACCOUNT_ID="${1:?usage: wa-pair <account-id>')
    assert idx_trap < idx_account, (
        "the EXIT trap must be registered before $1/$2 are read, so a "
        "missing-argument invocation is caught too")
    assert "JSON_TERMINAL_EMITTED" in WA_PAIR


def test_wa_pair_validation_refusal_emits_terminal_error_and_nothing_else():
    """An invalid/refused account id is the very first thing that can go
    wrong. In --json mode it must produce exactly one terminal error line
    and then stop — no event may follow it."""
    idx = WA_PAIR.index('TOKEN_VAR=$("$ROOT/bin/wa-reply" token-var "$ACCOUNT_ID") || {')
    block = WA_PAIR[idx:idx + 300]
    m = re.search(r"\|\| \{\n\s*emit_terminal error reason=.*?fatal=true\n\s*exit 1\n\}", block)
    assert m, (
        "the token-var failure branch must be exactly "
        "`emit_terminal error ...; exit 1`, nothing in between:\n" + block)


def test_wa_pair_history_purged_event_distinguishes_all_three_outcomes():
    """rows-purged-and-verified, rows-remaining/timed-out, and
    never-saw-a-row must be distinguishable by the GUI — the spec calls the
    last one uncertainty, not success, so it may not look identical to the
    success case."""
    never_saw = re.search(
        r'emit_json history_purged rows="\$PRE_PURGE" verified=false seen=false',
        WA_PAIR)
    remains = re.search(
        r'emit_json history_purged rows="\$PRE_PURGE" verified=false seen=true remaining="\$POST_PURGE"',
        WA_PAIR)
    success = re.search(
        r'emit_json history_purged rows="\$PRE_PURGE" verified=true seen=true',
        WA_PAIR)
    assert never_saw and remains and success, (
        "all three history outcomes must each emit a distinctly-shaped "
        "history_purged event")
    # The two failing outcomes must each be followed by a terminal event
    # before the process exits — never-saw-a-row exits 0 ("done", it is
    # not a failure, just uncertainty); rows-remaining/timed-out exits 1
    # ("error").
    after_never_saw = WA_PAIR[never_saw.end():never_saw.end() + 900]
    assert re.search(r"emit_terminal done\s*\n\s*exit 0", after_never_saw), after_never_saw
    after_remains = WA_PAIR[remains.end():remains.end() + 400]
    assert re.search(r"emit_terminal error .*fatal=true\s*\n\s*exit 1", after_remains, re.S), after_remains


def _extract_emit_json_python_body():
    """Pull the python program inside wa-pair's emit_json() bash function
    out of the source, so it can be executed directly. This is NOT running
    bin/wa-pair — it's the small, pure, side-effect-free JSON-building
    fragment on its own, fed controlled argv, never touching the network,
    docker or any real state."""
    m = re.search(r"emit_json\(\) \{.*?python3 -c '\n(.*?)\n'", WA_PAIR, re.S)
    assert m, "could not find emit_json()'s python body in bin/wa-pair"
    return m.group(1)


def test_emit_json_body_produces_one_valid_json_line_per_event():
    body = _extract_emit_json_python_body()
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(body)
        script_path = f.name
    cases = [
        ["validating", "account=trabajo"],
        ["code", "code=WNEH-WKK4"],
        ["linked"],
        ["history_waiting"],
        ["history_purged", "rows=15850", "verified=true", "seen=true"],
        ["history_purged", "rows=0", "verified=false", "seen=false"],
        ["history_purged", "rows=3", "verified=false", "seen=true", "remaining=3"],
        ["done"],
        ["error", 'reason=failed: HTTP 500 {"ok":false}', "fatal=true"],
    ]
    for args in cases:
        proc = subprocess.run(
            [sys.executable, script_path, *args],
            capture_output=True, text=True, check=True)
        lines = proc.stdout.splitlines()
        assert len(lines) == 1, f"expected exactly one line for {args}: {proc.stdout!r}"
        obj = json.loads(lines[0])  # must not raise
        assert obj["event"] == args[0]
        for kv in args[1:]:
            k, v = kv.split("=", 1)
            if v in ("true", "false"):
                assert obj[k] is (v == "true")
            elif v.lstrip("-").isdigit():
                assert obj[k] == int(v)
            else:
                assert obj[k] == v


# --- Task 1, fix round 1: the latch must not lie about a failed emission ---
#
# `bin/wa-pair` is never run for these either. The JSON-emission machinery
# (JSON_TERMINAL_EMITTED, emit_json, emit_terminal, on_exit, the trap
# registration) is self-contained bash with no network/docker/file-system
# side effects, so it is extracted verbatim from the source and run inside
# a small harness script under a real `bash` subprocess — the only way to
# observe trap/latch interaction honestly. What varies between harnesses is
# PATH: hiding `python3` behind an empty PATH is how a failing emission is
# simulated (python3 missing is one of the review's named real-world
# causes, and it fails exactly like OOM or a dead reader would: emit_json's
# python3 invocation returns non-zero without writing to stdout).
_BASH = shutil.which("bash") or "/usr/bin/bash"
assert Path(_BASH).exists(), "bash not found; cannot exercise the extracted JSON machinery"


def _extract_json_machinery():
    start = WA_PAIR.index("JSON_TERMINAL_EMITTED=0")
    end = WA_PAIR.index("trap on_exit EXIT") + len("trap on_exit EXIT")
    return WA_PAIR[start:end]


def _run_harness(trailer, no_python3):
    """Assemble JSON_MODE=1 + the extracted machinery + `trailer`, run it
    under bash, and return the finished process. `no_python3` hides python3
    from PATH so emit_json's underlying command fails the way it would if
    python3 were missing, out of memory, or writing to a closed pipe."""
    machinery = _extract_json_machinery()
    script = "set -euo pipefail\nJSON_MODE=1\n" + machinery + "\n" + trailer + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
        path = f.name
    env = {"PATH": "/nonexistent-omarchy-wa-test-path"} if no_python3 else {"PATH": "/usr/bin:/bin"}
    return subprocess.run([_BASH, path], capture_output=True, text=True, env=env)


def test_latch_is_not_set_when_the_emission_fails():
    """python3 unavailable -> emit_json fails -> emit_terminal must NOT
    latch JSON_TERMINAL_EMITTED. Fails against the pre-fix emit_terminal,
    which set the latch unconditionally before attempting the emission."""
    proc = _run_harness(
        'emit_terminal error reason=boom fatal=true || true\n'
        'echo "LATCH=$JSON_TERMINAL_EMITTED"',
        no_python3=True)
    assert "LATCH=0" in proc.stdout, proc.stdout + proc.stderr


def test_latch_is_set_when_the_emission_succeeds():
    """The positive control: with python3 available, the same call DOES
    latch — proving the fix didn't just stop latching altogether."""
    proc = _run_harness(
        'emit_terminal error reason=boom fatal=true || true\n'
        'echo "LATCH=$JSON_TERMINAL_EMITTED"',
        no_python3=False)
    assert "LATCH=1" in proc.stdout, proc.stdout + proc.stderr


def test_trap_fallback_still_emits_when_python3_is_unavailable():
    """The whole point of the backstop: even when the thing emit_json
    depends on (python3) is exactly what's broken, the EXIT trap still
    puts a terminal event on stdout, using a plain `printf` of a fixed
    string rather than the same machinery that just failed."""
    proc = _run_harness(
        'emit_terminal error reason=boom fatal=true || true\n'
        'exit 1',
        no_python3=True)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line: {proc.stdout!r}"
    obj = json.loads(lines[0])  # must not raise: a real, complete JSON line
    assert obj == {
        "event": "error",
        "reason": "wa-pair exited unexpectedly (exit code 1)",
        "fatal": True,
    }


def test_a_failing_emission_never_leaks_a_line_and_is_not_mistaken_for_success():
    """Nothing from the failed `emit_terminal error reason=boom ...` call
    may reach stdout — not a partial line, not the word "boom" anywhere —
    only the trap's own fixed fallback line."""
    proc = _run_harness(
        'emit_terminal error reason=boom fatal=true || true\n'
        'exit 1',
        no_python3=True)
    assert "boom" not in proc.stdout
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    json.loads(lines[0])  # the one line present is complete, valid JSON


def test_trap_fallback_is_skipped_once_a_real_terminal_event_went_out():
    """Normal path, unaffected by the fix: when emit_terminal succeeds
    before the script exits, the trap must NOT also emit its fallback —
    exactly one terminal event reaches stdout, not two."""
    proc = _run_harness(
        'emit_terminal error reason=boom fatal=true || true\n'
        'exit 1',
        no_python3=False)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one terminal event, got: {proc.stdout!r}"
    obj = json.loads(lines[0])
    assert obj == {"event": "error", "reason": "boom", "fatal": True}


if __name__ == "__main__":
    run(dict(globals()))
