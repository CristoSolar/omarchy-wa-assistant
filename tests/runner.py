# tests/runner.py
"""Minimal test runner: call every test_* in a module, report, exit non-zero on failure."""
import os
import sys
import tempfile
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_REAL_STATE_ROOT = (Path.home() / ".local/share/omarchy-wa").resolve()

# Structural isolation: every test process gets its own throwaway state root,
# set BEFORE any test module can import sink.store, call send()/_load_secrets()
# or otherwise resolve WA_DATA_DIR/WA_SECRETS_FILE. This is what makes "no
# test may touch the user's real state directory or credentials" a property
# of the test harness rather than something each test has to remember to set
# up — a test that forgets to override these still lands here, in a
# directory that (almost certainly) does not even exist, rather than in
# ~/.local/share/omarchy-wa where a real secrets.env with WA_DRY_RUN=0
# lives.
_session_tmp = tempfile.TemporaryDirectory(prefix="wa-assistant-tests-")
_THROWAWAY_ROOT = Path(_session_tmp.name).resolve()
os.environ["WA_STATE_ROOT"] = str(_THROWAWAY_ROOT)
os.environ["WA_DATA_DIR"] = str(_THROWAWAY_ROOT / "state")
os.environ["WA_SECRETS_FILE"] = str(_THROWAWAY_ROOT / "secrets.env")


def _guard_path(path: Path) -> Path:
    """Fail loudly if a resolved path lands under the user's real state dir."""
    resolved = Path(path).resolve()
    if resolved == _REAL_STATE_ROOT or _REAL_STATE_ROOT in resolved.parents:
        raise RuntimeError(
            "a test resolved a path under the user's real state directory "
            f"({_REAL_STATE_ROOT}): {resolved}. Every test must reach state "
            "through WA_DATA_DIR / WA_STATE_ROOT / WA_SECRETS_FILE overrides "
            "(tmpdir() from tests.runner), never the real default — a test "
            "run must be incapable of reading that person's correspondence "
            "or tokens.")
    return resolved


def _install_state_dir_guard():
    """Wrap sink.store's path resolvers so any escape to real state raises.

    This is a backstop, not the primary defense: the env overrides above
    already point every default away from the real location. This exists so
    that a future test/env var mix-up that somehow still resolves to
    ~/.local/share/omarchy-wa fails the test run loudly instead of quietly
    reading (or writing) the user's real queue, context, config or
    credentials.
    """
    from sink import store as _store

    for name in ("state_root", "data_dir", "secrets_path"):
        original = getattr(_store, name)

        def _wrapped(*a, _original=original, **k):
            result = _original(*a, **k)
            _guard_path(result)
            return result

        setattr(_store, name, _wrapped)


_install_state_dir_guard()


def tmpdir():
    """Context manager yielding a fresh Path, the stdlib stand-in for tmp_path."""
    class _Ctx:
        def __enter__(self):
            self._t = tempfile.TemporaryDirectory()
            return Path(self._t.name)

        def __exit__(self, *a):
            self._t.cleanup()
            return False
    return _Ctx()


def run(namespace):
    tests = [(k, v) for k, v in sorted(namespace.items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
