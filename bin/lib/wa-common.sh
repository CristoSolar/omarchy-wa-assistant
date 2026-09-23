# bin/lib/wa-common.sh — the shared --json event machinery.
#
# SOURCED, never executed: it defines functions and installs an EXIT trap
# in the caller's own shell. bin/wa-pair and bin/wa-unlink each carried a
# verbatim copy of everything below; two copies of the one mechanism that
# guarantees a GUI never waits forever is one copy too many — a fix to one
# would have silently left the other broken.
#
# The caller must, BEFORE sourcing this file:
#   * set JSON_MODE to 1 (--json) or 0 (human mode), having already
#     stripped the flag out of "$@";
#   * set WA_EVENT_SCRIPT to its own name ("wa-pair", "wa-unlink"), which
#     is the only thing that varies between the two copies this replaces.
#     It appears in the EXIT trap's fallback line, so it must be a fixed
#     literal the script itself supplies, never anything user-controlled.
#
# A caller that cannot source this file must abort LOUDLY. Running on
# without it would leave emit_json undefined: under `set -u`/`set -e` that
# aborts anyway, but in a shell without them it would degrade into a
# script that does its work while emitting no events at all — a GUI
# spinner that never resolves, which is the exact failure this machinery
# exists to rule out. Both callers guard the `source` with an explicit
# check and print a JSON error line of their own before exiting.

# Every path that can end a caller must emit a terminal ("done" or
# "error") event in --json mode — a run that stops without one leaves a
# GUI showing a spinner forever. The trap below is registered the moment
# this file is sourced, which the callers do BEFORE parsing their
# positional args, so even a missing-argument usage error (an `exit` bash
# takes on its own from `${1:?...}` under `set -u`) is caught.
#
# Each known exit point in a caller emits its OWN specific terminal event
# (with a real reason) via emit_terminal() before exiting; emit_terminal()
# latches JSON_TERMINAL_EMITTED, but ONLY once the emission actually
# succeeded, so the trap knows one really went out. The trap is the
# backstop for everything NOT anticipated explicitly.
#
# The real guarantee this gives: every exit bash itself can observe — a
# normal `exit`, or the ones set -e/set -u take on their own — ends the
# stream with a terminal event. A hard kill (SIGKILL) cannot run an EXIT
# trap; no userspace code can change that. A GUI reading this stream must
# still treat "the process ended with no terminal event" as a real, if
# rare, outcome — not something these scripts can promise away.
JSON_TERMINAL_EMITTED=0

emit_json() {
  # $1 = event name; remaining args are "key=value" pairs. A value of
  # "true"/"false" becomes a JSON bool, an integer-looking value becomes a
  # JSON number, anything else is a JSON string. Each call is its own
  # python3 process that prints one line and exits, so the line is already
  # flushed by the time this function returns — nothing here buffers
  # across events. A no-op in human mode. Returns non-zero if python3
  # could not run or the write failed (e.g. python3 missing, OOM, disk
  # full, or the reader's end of the pipe is already gone) — callers that
  # need to know whether the line actually reached stdout must check this.
  (( JSON_MODE )) || return 0
  python3 -c '
import json, sys
obj = {"event": sys.argv[1]}
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    if v in ("true", "false"):
        obj[k] = (v == "true")
    else:
        try:
            obj[k] = int(v)
        except ValueError:
            obj[k] = v
print(json.dumps(obj), flush=True)
' "$@"
}

emit_terminal() {
  # The latch must only ever claim a terminal event that actually reached
  # stdout. Setting it unconditionally BEFORE calling emit_json (as an
  # earlier version of this function did) would let a failed emission —
  # python3 missing or broken, OOM, disk full, a reader that already hung
  # up — set the latch anyway; under `set -e` that failure then aborts the
  # script, the EXIT trap fires, sees the latch already set, and skips its
  # own fallback believing an event went out that never did. That is
  # exactly the silent-stream failure this whole mechanism exists to rule
  # out, so the latch is set ONLY after emit_json reports success.
  if emit_json "$@"; then
    JSON_TERMINAL_EMITTED=1
  fi
}

on_exit() {
  local code=$?
  if (( JSON_MODE )) && (( ! JSON_TERMINAL_EMITTED )); then
    # Deliberately NOT emit_json/emit_terminal here: if python3 itself is
    # what is broken (missing, OOM, whatever aborted the script in the
    # first place), a fallback that also shells out to python3 would fail
    # the exact same way and leave stdout just as silent. This fallback
    # carries no user-controlled data — the script name is a literal the
    # caller set — so a plain `printf` has no escaping to get wrong and no
    # dependency on the thing that may have just died.
    if (( code == 0 )); then
      printf '%s\n' '{"event":"done"}'
    else
      printf '{"event":"error","reason":"%s exited unexpectedly (exit code %d)","fatal":true}\n' \
        "$WA_EVENT_SCRIPT" "$code"
    fi
  fi
  exit "$code"
}
trap on_exit EXIT

wa_token_var() {
  # $1 = path to bin/wa-reply, $2 = account id. Runs `wa-reply token-var`
  # exactly ONCE and sets two variables from that single run:
  #   TOKEN_VAR      the token's variable name (stdout), on success
  #   TOKEN_VAR_ERR  the refusal text (stderr), on failure
  # Returns wa-reply's own exit status.
  #
  # Both callers used to run it TWICE in --json mode — once discarding
  # stdout to capture stderr for the error event, then again for the value
  # — which is two processes, two validations, and two chances for the two
  # runs to disagree. stdout and stderr are kept apart (rather than merged
  # with 2>&1) on purpose: anything python3 might print on stderr on an
  # otherwise successful run would otherwise be concatenated into
  # TOKEN_VAR, and a wrong variable name silently fails to match the line
  # in secrets.env that wa-unlink is trying to remove.
  #
  # The account id is validated in exactly one place — accounts.validate_id,
  # which `wa-reply token-var` reaches — never re-derived in bash. Nothing
  # here inspects or second-guesses the id.
  local wa_reply="$1" account_id="$2" err_file rc
  TOKEN_VAR=""
  TOKEN_VAR_ERR=""
  err_file=$(mktemp "${TMPDIR:-/tmp}/wa-token-var.XXXXXX" 2>/dev/null) || err_file=""
  if [[ -n "$err_file" ]]; then
    TOKEN_VAR=$("$wa_reply" token-var "$account_id" 2>"$err_file")
    rc=$?
    TOKEN_VAR_ERR=$(cat "$err_file" 2>/dev/null)
    rm -f "$err_file"
  else
    # mktemp itself failed (a full or read-only /tmp). Losing the refusal
    # text is worth less than losing the run: fall back to the value alone
    # and let the caller use its generic reason.
    TOKEN_VAR=$("$wa_reply" token-var "$account_id" 2>/dev/null)
    rc=$?
  fi
  return "$rc"
}
