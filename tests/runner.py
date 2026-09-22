# tests/runner.py
"""Minimal test runner: call every test_* in a module, report, exit non-zero on failure."""
import tempfile
import traceback
from pathlib import Path


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
