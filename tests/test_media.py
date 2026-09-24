# tests/test_media.py
"""Tests for bin/wa-media: never touch the network (see tests/runner.py's
state-dir guard), only exercise the account rule, the filename-safety
hashing, idempotency and pruning.
"""
import base64
import importlib.util
import json
import os
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.runner import run, tmpdir
from sink import store

# bin/wa-media has no .py extension, same trick test_reply.py uses.
_loader = SourceFileLoader("wa_media", str(ROOT / "bin" / "wa-media"))
wm = importlib.util.module_from_spec(importlib.util.spec_from_loader("wa_media", _loader))
_loader.exec_module(wm)


PERSONAL = {"id": "personal", "label": "Personal", "wuzapi_user": "omarchy",
            "agent": "respuesta.md", "quick_replies": []}
TOKENS = {"personal": "tok-personal"}

FULL_FIELDS = {
    "URL": "https://mmg.whatsapp.net/x", "mimetype": "image/jpeg",
    "mediaKey": "bWVkaWFrZXk=", "fileSHA256": "c2hh1",
    "fileEncSHA256": "ZW5jc2hh", "fileLength": 1234,
    "directPath": "/v/t62/abc",
}

FAKE_IMAGE_BYTES = b"\xff\xd8\xff not really a jpeg but bytes are bytes"


def _prepare(d, accts=(PERSONAL,), tokens=TOKENS):
    (d / "accounts.json").write_text(json.dumps(list(accts), ensure_ascii=False))
    if tokens is not None:
        (d / "secrets.env").write_text(
            "".join(f"{wm.accounts.token_env_var(k)}={v}\n" for k, v in tokens.items()))
    return d


def _entry(entry_id="personal:1@s.whatsapp.net", account="personal",
           downloadable=True, atype="image", extra_fields=True):
    attachment = {"type": atype, "downloadable": downloadable}
    if downloadable and extra_fields:
        attachment.update(FULL_FIELDS)
    return {"id": entry_id, "jid": "1@s.whatsapp.net", "account": account,
            "push_name": "Ana", "text": "[imagen]", "count": 1,
            "ts": "2026-09-22T10:00:00", "status": "pending",
            "attachment": attachment}


class _media_env:
    """Point wa-media at a throwaway state dir for one test."""

    def __init__(self, d):
        self.d = d
        self.saved = {}

    def __enter__(self):
        for var in ("WA_STATE_ROOT", "WA_DATA_DIR", "WA_SECRETS_FILE", "WA_API"):
            self.saved[var] = os.environ.get(var)
        for key in [k for k in os.environ if k.startswith("WA_TOKEN")]:
            self.saved.setdefault(key, os.environ[key])
            del os.environ[key]
        os.environ["WA_STATE_ROOT"] = str(self.d)
        os.environ["WA_DATA_DIR"] = str(self.d / "state")
        os.environ["WA_SECRETS_FILE"] = str(self.d / "secrets.env")
        os.environ["WA_API"] = "http://127.0.0.1:8080"
        wm.API = os.environ["WA_API"]
        return store.data_dir()

    def __exit__(self, *a):
        for var, val in self.saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        return False


def _fake_download(mimetype=b"image/jpeg", body=FAKE_IMAGE_BYTES):
    calls = []

    def _fake(account, attachment, entry_id):
        calls.append((account["id"], attachment["type"], entry_id))
        return body

    return calls, _fake


# --- The account rule -------------------------------------------------------


def test_the_account_comes_from_the_entry_and_nothing_else():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry())
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            path = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert calls == [("personal", "image", "personal:1@s.whatsapp.net")]
        assert path.read_bytes() == FAKE_IMAGE_BYTES


def test_an_unknown_account_is_refused_without_downloading():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        entry = _entry(entry_id="ghost:1@s.whatsapp.net", account="ghost")
        store.upsert_queue(d, entry)
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            try:
                wm.get_media_path(d, "ghost:1@s.whatsapp.net")
                assert False, "expected UnknownAccount"
            except wm.UnknownAccount as exc:
                assert exc.payload["unknown_account"] == "ghost"
        finally:
            wm._download_bytes = original
        assert calls == [], "an unknown account must never reach the download"


def test_an_entry_with_no_account_field_is_refused_without_downloading():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        entry = _entry()
        del entry["account"]
        entry["id"] = "noaccount:1@s.whatsapp.net"
        store.upsert_queue(d, entry)
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            try:
                wm.get_media_path(d, "noaccount:1@s.whatsapp.net")
                assert False, "expected AccountNotStated"
            except wm.AccountNotStated:
                pass
        finally:
            wm._download_bytes = original
        assert calls == []


# --- Not-downloadable is a distinct refusal ---------------------------------


def test_a_not_downloadable_attachment_is_refused_distinctly():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry(downloadable=False, atype="location"))
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            try:
                wm.get_media_path(d, "personal:1@s.whatsapp.net")
                assert False, "expected NotDownloadable"
            except wm.NotDownloadable as exc:
                assert exc.payload["downloadable"] is False
                assert not isinstance(exc, wm.UnknownAccount)
        finally:
            wm._download_bytes = original
        assert calls == []


def test_a_downloadable_type_missing_a_required_field_is_refused_too():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry(extra_fields=False))
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            try:
                wm.get_media_path(d, "personal:1@s.whatsapp.net")
                assert False, "expected NotDownloadable"
            except wm.NotDownloadable:
                pass
        finally:
            wm._download_bytes = original
        assert calls == []


def test_a_missing_entry_is_refused_without_downloading():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            try:
                wm.get_media_path(d, "personal:doesnotexist@s.whatsapp.net")
                assert False, "expected EntryNotFound"
            except wm.EntryNotFound:
                pass
        finally:
            wm._download_bytes = original
        assert calls == []


# --- Filename safety ---------------------------------------------------------


HOSTILE_IDS = [
    "personal:../../../../etc/passwd",
    "personal:/etc/passwd",
    "personal:..",
    "personal:a/../../b",
    "personal:\x00nullbyte",
    "personal:" + ("x" * 4000),
    "personal:..%2f..%2fetc%2fpasswd",
    "personal:con",  # legal JSON, historically hostile on some filesystems
]


def test_a_hostile_message_id_cannot_escape_the_cache_directory():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        root = wm.media_root()
        for hostile_id in HOSTILE_IDS:
            entry = _entry(entry_id=hostile_id)
            store.upsert_queue(d, entry)
            calls, fake = _fake_download()
            original, wm._download_bytes = wm._download_bytes, fake
            try:
                path = wm.get_media_path(d, hostile_id)
            finally:
                wm._download_bytes = original
            resolved = path.resolve()
            assert str(resolved).startswith(str(root.resolve()) + os.sep), (
                f"hostile id {hostile_id!r} produced a path outside the cache: "
                f"{resolved}")
            # Never any literal fragment of the hostile id in the final name.
            assert ".." not in path.name and "/" not in path.name
            assert path.exists()
            store.remove_queue(d, entry["id"])


def test_two_different_hostile_ids_never_collide_on_the_same_file():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        paths = set()
        for i, hostile_id in enumerate(HOSTILE_IDS):
            entry = _entry(entry_id=f"personal:distinct-{i}")
            store.upsert_queue(d, entry)
            calls, fake = _fake_download()
            original, wm._download_bytes = wm._download_bytes, fake
            try:
                path = wm.get_media_path(d, entry["id"])
            finally:
                wm._download_bytes = original
            paths.add(path)
        assert len(paths) == len(HOSTILE_IDS)


# --- Idempotency --------------------------------------------------------------


def test_a_second_call_for_the_same_attachment_does_not_re_download():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry())
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            path1 = wm.get_media_path(d, "personal:1@s.whatsapp.net")
            path2 = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert path1 == path2
        assert len(calls) == 1, "the second get must not call the network layer"


def test_a_zero_byte_cached_file_is_not_trusted_and_is_re_downloaded():
    """A truncated/empty file at the expected path (disk-full, a killed
    write that skipped cleanup, a person poking at the cache) must not be
    served silently — it must trigger a fresh download instead."""
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        entry = _entry()
        store.upsert_queue(d, entry)
        attachment = entry["attachment"]
        path = wm._cached_path("personal", entry["id"], attachment)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")  # corrupt: zero bytes where a real file should be

        calls, fake = _fake_download(body=FAKE_IMAGE_BYTES)
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            result = wm.get_media_path(d, entry["id"])
        finally:
            wm._download_bytes = original

        assert len(calls) == 1, "a corrupt cache hit must trigger exactly one re-download"
        assert result.read_bytes() == FAKE_IMAGE_BYTES


def test_a_new_attachment_on_the_same_jid_gets_its_own_cache_key():
    """upsert_queue overwrites the entry's attachment in place (same entry
    id), but a different fileSHA256 must still produce a fresh file rather
    than silently reusing a stale cached image."""
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry())
        calls, fake = _fake_download(body=b"first photo")
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            path1 = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert path1.read_bytes() == b"first photo"

        second = _entry()
        second["attachment"]["fileSHA256"] = "different-hash"
        store.upsert_queue(d, second)
        calls2, fake2 = _fake_download(body=b"second photo")
        original, wm._download_bytes = wm._download_bytes, fake2
        try:
            path2 = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert path2 != path1
        assert path2.read_bytes() == b"second photo"
        assert len(calls2) == 1


# --- Pruning -------------------------------------------------------------------


def test_pruning_leaves_a_concurrent_temp_file_alone():
    """A second, in-flight `wa-media get` stages its write with mkstemp in
    the same account directory this call's prune pass sweeps. That temp
    file must never be picked up as an age or size victim — it belongs to
    a write nobody has finished yet, and deleting it would make the other
    process's later os.replace fail with a missing source."""
    with tmpdir() as t, _media_env(t) as d:
        config = {"media_cache_max_bytes": 1, "media_cache_max_age_days": 3650}
        (d / "config.json").write_text(json.dumps(config))
        _prepare(d)
        account_dir = wm._account_dir("personal")
        concurrent_tmp = account_dir / "tmpxyz123.tmp"
        concurrent_tmp.write_bytes(b"partially written by another process")
        old_time = time.time() - 1000
        os.utime(concurrent_tmp, (old_time, old_time))

        store.upsert_queue(d, _entry())
        calls, fake = _fake_download(body=FAKE_IMAGE_BYTES)
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original

        assert concurrent_tmp.exists(), (
            "a prune pass must never delete another invocation's in-progress "
            "temp file")


def test_pruning_respects_the_age_limit():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        root = wm.media_root()
        account_dir = root / "personal"
        account_dir.mkdir(parents=True, exist_ok=True)
        old = account_dir / ("a" * 64 + ".jpg")
        old.write_bytes(b"old")
        old_time = time.time() - 40 * 86400  # 40 days, older than the 30-day default
        os.utime(old, (old_time, old_time))

        store.upsert_queue(d, _entry())
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            new_path = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original

        assert not old.exists(), "a file older than max_age_days must be pruned"
        assert new_path.exists()


def test_pruning_respects_the_size_limit_oldest_first():
    with tmpdir() as t, _media_env(t) as d:
        config = {"media_cache_max_bytes": 20, "media_cache_max_age_days": 3650}
        (d / "config.json").write_text(json.dumps(config))
        _prepare(d)
        root = wm.media_root()
        account_dir = root / "personal"
        account_dir.mkdir(parents=True, exist_ok=True)

        older = account_dir / ("b" * 64 + ".jpg")
        older.write_bytes(b"0123456789")  # 10 bytes
        os.utime(older, (time.time() - 100, time.time() - 100))

        newer_unrelated = account_dir / ("c" * 64 + ".jpg")
        newer_unrelated.write_bytes(b"0123456789")  # 10 bytes
        os.utime(newer_unrelated, (time.time() - 50, time.time() - 50))

        store.upsert_queue(d, _entry())
        calls, fake = _fake_download(body=b"0123456789")  # 10 bytes, pushes total to 30
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            new_path = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original

        assert not older.exists(), "the oldest file must be pruned first over budget"
        assert new_path.exists(), "the file just written must survive pruning"


def test_pruning_never_removes_the_file_just_written_even_under_a_tiny_budget():
    with tmpdir() as t, _media_env(t) as d:
        config = {"media_cache_max_bytes": 1, "media_cache_max_age_days": 3650}
        (d / "config.json").write_text(json.dumps(config))
        _prepare(d)
        store.upsert_queue(d, _entry())
        calls, fake = _fake_download(body=FAKE_IMAGE_BYTES)
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            new_path = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert new_path.exists(), (
            "even with a budget of 1 byte, the file just written is never "
            "the one pruning removes")


# --- Size --------------------------------------------------------------------


def test_size_of_an_empty_cache_is_zero():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        assert wm.cache_size() == 0
        assert wm.cache_size("personal") == 0


def test_size_counts_the_whole_cache_across_accounts():
    trabajo = {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo",
               "agent": "respuesta-trabajo.md", "quick_replies": []}
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d, accts=(PERSONAL, trabajo),
                 tokens={"personal": "tok-personal", "trabajo": "tok-trabajo"})
        store.upsert_queue(d, _entry())
        entry_trabajo = _entry(entry_id="trabajo:2@s.whatsapp.net", account="trabajo")
        store.upsert_queue(d, entry_trabajo)

        calls, fake = _fake_download(body=b"12345")  # 5 bytes
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        calls2, fake2 = _fake_download(body=b"1234567")  # 7 bytes
        original, wm._download_bytes = wm._download_bytes, fake2
        try:
            wm.get_media_path(d, "trabajo:2@s.whatsapp.net")
        finally:
            wm._download_bytes = original

        assert wm.cache_size() == 12
        assert wm.cache_size("personal") == 5
        assert wm.cache_size("trabajo") == 7


def test_size_rejects_a_hostile_account_argument():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        try:
            wm.cache_size("../../etc")
            assert False, "expected a refusal"
        except ValueError:
            pass


def test_size_reflects_a_clear():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry())
        calls, fake = _fake_download(body=b"12345")
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert wm.cache_size() == 5
        wm.clear_cache()
        assert wm.cache_size() == 0


# --- Clearing --------------------------------------------------------------------


def test_clear_whole_cache_removes_everything_and_leaves_a_valid_state():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        store.upsert_queue(d, _entry())
        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            path = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert path.exists()

        wm.clear_cache()

        root = wm.media_root()
        assert not path.exists()
        assert root.is_dir()
        assert oct(root.stat().st_mode & 0o777) == oct(0o700)
        # A subsequent get still works: clearing left a usable cache, not a
        # broken one.
        calls2, fake2 = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake2
        try:
            path2 = wm.get_media_path(d, "personal:1@s.whatsapp.net")
        finally:
            wm._download_bytes = original
        assert path2.exists()


def test_clear_one_account_leaves_other_accounts_cache_intact():
    trabajo = {"id": "trabajo", "label": "Trabajo", "wuzapi_user": "trabajo",
               "agent": "respuesta-trabajo.md", "quick_replies": []}
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d, accts=(PERSONAL, trabajo),
                 tokens={"personal": "tok-personal", "trabajo": "tok-trabajo"})
        store.upsert_queue(d, _entry())
        entry_trabajo = _entry(entry_id="trabajo:2@s.whatsapp.net", account="trabajo")
        store.upsert_queue(d, entry_trabajo)

        calls, fake = _fake_download()
        original, wm._download_bytes = wm._download_bytes, fake
        try:
            path_personal = wm.get_media_path(d, "personal:1@s.whatsapp.net")
            path_trabajo = wm.get_media_path(d, "trabajo:2@s.whatsapp.net")
        finally:
            wm._download_bytes = original

        wm.clear_cache("personal")

        assert not path_personal.exists()
        assert path_trabajo.exists(), "clearing one account must not touch another's cache"


def test_clear_rejects_a_hostile_account_argument():
    with tmpdir() as t, _media_env(t) as d:
        _prepare(d)
        try:
            wm.clear_cache("../../etc")
            assert False, "expected a refusal"
        except ValueError:
            pass


# --- Limits: a hand-edited config.json must never disable the cap ------------


def test_limits_fall_back_to_defaults_on_a_negative_max_bytes():
    with tmpdir() as t, _media_env(t) as d:
        (d / "config.json").write_text(json.dumps({"media_cache_max_bytes": -1}))
        max_bytes, max_age_days = wm._limits(d)
        assert max_bytes == wm.DEFAULT_MAX_BYTES
        assert max_age_days == wm.DEFAULT_MAX_AGE_DAYS


def test_limits_fall_back_to_defaults_on_a_zero_max_age():
    with tmpdir() as t, _media_env(t) as d:
        (d / "config.json").write_text(json.dumps({"media_cache_max_age_days": 0}))
        max_bytes, max_age_days = wm._limits(d)
        assert max_bytes == wm.DEFAULT_MAX_BYTES
        assert max_age_days == wm.DEFAULT_MAX_AGE_DAYS


def test_limits_fall_back_to_defaults_on_a_non_numeric_value():
    with tmpdir() as t, _media_env(t) as d:
        (d / "config.json").write_text(json.dumps({
            "media_cache_max_bytes": "unlimited",
            "media_cache_max_age_days": "forever",
        }))
        max_bytes, max_age_days = wm._limits(d)
        assert max_bytes == wm.DEFAULT_MAX_BYTES
        assert max_age_days == wm.DEFAULT_MAX_AGE_DAYS


def test_limits_honour_a_valid_configured_value():
    with tmpdir() as t, _media_env(t) as d:
        (d / "config.json").write_text(json.dumps({
            "media_cache_max_bytes": 42, "media_cache_max_age_days": 7,
        }))
        assert wm._limits(d) == (42, 7)


if __name__ == "__main__":
    run(globals())
