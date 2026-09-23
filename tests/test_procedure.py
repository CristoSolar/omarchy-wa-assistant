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
import re
import sys
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


if __name__ == "__main__":
    run(dict(globals()))
