import json, os, sys
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runner import run, tmpdir
from sink import store


@contextmanager
def _no_data_dir_or_secrets_override():
    """Pop WA_DATA_DIR/WA_SECRETS_FILE for the duration of the block.

    tests/runner.py sets both, process-wide, to a single throwaway
    location so no test can ever reach the user's real state. That is
    exactly what migrate_single_account must now consult (I1: it resolves
    data_dir/secrets_file the same way store.data_dir()/secrets_path() do,
    env override first), so a migration test working against its own
    per-test tmpdir has to clear those two overrides first — same pattern
    as test_the_sink_data_dir_is_a_subdirectory_of_the_state_root below.
    """
    previous_dir = os.environ.pop("WA_DATA_DIR", None)
    previous_secrets = os.environ.pop("WA_SECRETS_FILE", None)
    try:
        yield
    finally:
        if previous_dir is not None:
            os.environ["WA_DATA_DIR"] = previous_dir
        if previous_secrets is not None:
            os.environ["WA_SECRETS_FILE"] = previous_secrets


def _migrate(state_root):
    with _no_data_dir_or_secrets_override():
        return store.migrate_single_account(state_root)


def _entry(jid="56911111111@s.whatsapp.net", text="hola", name="Ana"):
    return {"id": jid, "jid": jid, "push_name": name, "text": text,
            "count": 1, "ts": "2026-09-22T10:00:00", "status": "pending"}


def test_upsert_collapses_repeat_messages_from_one_chat():
    with tmpdir() as d:
        store.upsert_queue(d, _entry(text="hola"))
        q = store.upsert_queue(d, _entry(text="estas?"))
        assert len(q) == 1, "same chat must not create a second entry"
        assert q[0]["text"] == "estas?", "entry must carry the latest text"
        assert q[0]["count"] == 2, "entry must count the unread messages"


def test_upsert_keeps_separate_chats_apart():
    with tmpdir() as d:
        store.upsert_queue(d, _entry(jid="1@s.whatsapp.net"))
        q = store.upsert_queue(d, _entry(jid="2@s.whatsapp.net"))
        assert len(q) == 2


def test_missing_queue_file_reads_as_empty():
    with tmpdir() as d:
        assert store.load_queue(d) == []


def test_corrupt_queue_file_raises_instead_of_reading_as_empty():
    # A file that EXISTS but is not valid JSON is a broken install, not an
    # empty queue — this is defect 2: read_json used to swallow this into
    # the same default as "no queue yet", so `wa-reply list` printed a
    # confident `[]` for a corrupt file. It must raise instead.
    with tmpdir() as d:
        (d / "queue.json").write_text("{not json")
        try:
            store.load_queue(d)
            assert False, "expected StoreReadError for invalid JSON"
        except store.StoreReadError as exc:
            assert "queue.json" in str(exc)


def test_unreadable_queue_file_raises_instead_of_reading_as_empty():
    # A file that EXISTS but cannot be opened (permissions — the root-owned
    # state file from defect 1) must be told apart from "no queue yet" too.
    import os
    with tmpdir() as d:
        p = d / "queue.json"
        p.write_text("[]")
        os.chmod(p, 0o000)
        try:
            if os.access(p, os.R_OK):
                # Running as root (or some other setup where permission bits
                # don't apply): nothing to test here.
                return
            try:
                store.load_queue(d)
                assert False, "expected StoreReadError for an unreadable file"
            except store.StoreReadError as exc:
                assert "queue.json" in str(exc)
        finally:
            os.chmod(p, 0o600)


def test_context_ring_stops_at_ten():
    with tmpdir() as d:
        jid = "56911111111@s.whatsapp.net"
        for i in range(25):
            ring = store.append_context(d, jid, {"text": f"m{i}"}, "personal")
        assert len(ring) == 10
        assert ring[-1]["text"] == "m24", "newest message must be last"
        assert ring[0]["text"] == "m15", "oldest must be dropped"


def test_atomic_write_leaves_no_partial_file():
    with tmpdir() as d:
        p = d / "queue.json"
        store.atomic_write(p, json.dumps([{"id": "x"}]))
        assert json.loads(p.read_text())[0]["id"] == "x"
        assert list(d.glob("*.tmp*")) == [], "temp file must not survive"


def test_upsert_tolerates_an_entry_without_a_push_name():
    with tmpdir() as d:
        store.upsert_queue(d, _entry(name="Ana"))
        e2 = _entry(text="de nuevo")
        del e2["push_name"]
        q = store.upsert_queue(d, e2)
        assert q[0]["push_name"] == "Ana", "existing display name must survive a missing push_name"


def test_load_quick_replies_defaults_to_empty_list():
    with tmpdir() as d:
        assert store.load_quick_replies(d) == []


def test_load_quick_replies_reads_the_configured_list_without_disturbing_order():
    with tmpdir() as d:
        (d / "config.json").write_text(
            json.dumps({"groups_enabled": ["1@g.us"],
                       "quick_replies": ["Uno", "Dos", "Tres"]}))
        assert store.load_quick_replies(d) == ["Uno", "Dos", "Tres"]


def test_set_status_and_remove():
    with tmpdir() as d:
        e = _entry()
        store.upsert_queue(d, e)
        store.set_status(d, e["id"], "sending")
        assert store.load_queue(d)[0]["status"] == "sending"
        store.remove_queue(d, e["id"])
        assert store.load_queue(d) == []


def test_the_sink_data_dir_is_a_subdirectory_of_the_state_root():
    # I2: the sink's bind mount is WA_DATA_DIR, so it must not be the same
    # directory that holds secrets.env.
    import os
    previous_root = os.environ.get("WA_STATE_ROOT")
    previous_dir = os.environ.pop("WA_DATA_DIR", None)
    # The test harness (tests/runner.py) sets WA_SECRETS_FILE on every test
    # process so no test can ever fall back to the real secrets.env; that
    # override takes precedence over WA_STATE_ROOT by design (see
    # store.secrets_path()), so it must come off here too to observe the
    # plain state-root-derived default this test is actually about.
    previous_secrets_file = os.environ.pop("WA_SECRETS_FILE", None)
    with tmpdir() as d:
        os.environ["WA_STATE_ROOT"] = str(d)
        try:
            assert store.state_root() == d
            assert store.data_dir() == d / "state"
            assert store.secrets_path() == d / "secrets.env"
            assert store.secrets_path().parent != store.data_dir()
        finally:
            if previous_dir is not None:
                os.environ["WA_DATA_DIR"] = previous_dir
            if previous_root is None:
                os.environ.pop("WA_STATE_ROOT", None)
            else:
                os.environ["WA_STATE_ROOT"] = previous_root
            if previous_secrets_file is not None:
                os.environ["WA_SECRETS_FILE"] = previous_secrets_file


def test_the_secrets_path_has_its_own_override():
    import os
    previous_file = os.environ.get("WA_SECRETS_FILE")
    with tmpdir() as d:
        os.environ["WA_SECRETS_FILE"] = str(d / "elsewhere.env")
        try:
            assert store.secrets_path() == d / "elsewhere.env"
        finally:
            if previous_file is None:
                os.environ.pop("WA_SECRETS_FILE", None)
            else:
                os.environ["WA_SECRETS_FILE"] = previous_file


def test_a_new_message_does_not_clear_an_unconfirmed_send():
    # The "unknown" status is the durable half of the double-send guard.
    # An incoming message must not unlock it from the outside.
    with tmpdir() as d:
        store.upsert_queue(d, _entry())
        store.set_status(d, _entry()["id"], "unknown")
        q = store.upsert_queue(d, _entry(text="seguis ahi?"))
        assert q[0]["text"] == "seguis ahi?"
        assert q[0]["status"] == "unknown"


def test_a_new_message_does_reset_a_failed_entry_to_pending():
    with tmpdir() as d:
        store.upsert_queue(d, _entry())
        store.set_status(d, _entry()["id"], "failed")
        q = store.upsert_queue(d, _entry(text="seguis ahi?"))
        assert q[0]["status"] == "pending"


def test_a_new_message_does_not_clear_an_interrupted_send():
    # "sending" left behind by a wa-reply that was killed mid-POST is the
    # same ambiguity as "unknown"; an inbound message must not clear it.
    with tmpdir() as d:
        store.upsert_queue(d, _entry())
        store.set_status(d, _entry()["id"], "sending")
        q = store.upsert_queue(d, _entry(text="seguis ahi?"))
        assert q[0]["text"] == "seguis ahi?"
        assert q[0]["status"] == "sending"


def test_same_jid_under_two_accounts_does_not_collide():
    with tmpdir() as d:
        jid = "56911111111@s.whatsapp.net"
        store.upsert_queue(d, {**_entry(jid=jid), "id": store.entry_id("personal", jid),
                               "account": "personal"})
        q = store.upsert_queue(d, {**_entry(jid=jid), "id": store.entry_id("trabajo", jid),
                                   "account": "trabajo"})
        assert len(q) == 2, "the same contact on two accounts must be two entries"
        ids = {e["id"] for e in q}
        assert ids == {"personal:" + jid, "trabajo:" + jid}


def test_entry_id_and_split_entry_id_round_trip():
    jid = "56911111111@s.whatsapp.net"
    eid = store.entry_id("personal", jid)
    assert eid == "personal:" + jid
    assert store.split_entry_id(eid) == ("personal", jid)


def test_entry_id_round_trip_with_a_colon_in_the_jid():
    # Real JIDs on LID accounts look like "237786636488820:23@lid". A naive
    # rsplit or split-all would cut this in the wrong place and resolve the
    # entry to the wrong chat.
    jid = "237786636488820:23@lid"
    eid = store.entry_id("personal", jid)
    assert eid == "personal:237786636488820:23@lid"
    account_id, recovered_jid = store.split_entry_id(eid)
    assert account_id == "personal"
    assert recovered_jid == jid


def test_context_for_one_account_is_invisible_to_another():
    with tmpdir() as d:
        jid = "56911111111@s.whatsapp.net"
        store.append_context(d, jid, {"text": "hola personal"}, account="personal")
        store.append_context(d, jid, {"text": "hola trabajo"}, account="trabajo")
        personal_ring = store.load_context(d, jid, account="personal")
        trabajo_ring = store.load_context(d, jid, account="trabajo")
        assert [c["text"] for c in personal_ring] == ["hola personal"]
        assert [c["text"] for c in trabajo_ring] == ["hola trabajo"]


def _pre_migration_layout(d):
    """A realistic single-account, pre-migration state root."""
    state_root = d
    data_dir = state_root / "state"
    (data_dir / "ctx").mkdir(parents=True)
    (state_root).mkdir(exist_ok=True)
    secrets = state_root / "secrets.env"
    secrets.write_text(
        "WUZAPI_ADMIN_TOKEN=abc123\n"
        "WUZAPI_GLOBAL_ENCRYPTION_KEY=def456\n"
        "WA_TOKEN=tok-personal-789\n"
    )
    queue = [
        {"id": "1@s.whatsapp.net", "jid": "1@s.whatsapp.net", "push_name": "Ana",
         "text": "hola", "count": 1, "ts": "2026-09-22T10:00:00", "status": "pending"},
        {"id": "2@s.whatsapp.net", "jid": "2@s.whatsapp.net", "push_name": "Beto",
         "text": "que tal", "count": 2, "ts": "2026-09-22T11:00:00", "status": "sending"},
        {"id": "237786636488820:23@lid", "jid": "237786636488820:23@lid",
         "push_name": "Cami", "text": "che", "count": 1,
         "ts": "2026-09-22T12:00:00", "status": "unknown"},
    ]
    (data_dir / "queue.json").write_text(json.dumps(queue, ensure_ascii=False))
    (data_dir / "config.json").write_text(json.dumps(
        {"groups_enabled": ["1@g.us"], "quick_replies": ["Uno", "Dos"]}))
    for jid in ("1@s.whatsapp.net", "2@s.whatsapp.net"):
        safe = jid.replace("/", "_")
        (data_dir / "ctx" / f"{safe}.json").write_text(
            json.dumps([{"from": "x", "text": "hi", "ts": "2026-09-22T09:00:00"}]))
    # The colon-bearing jid needs its ctx filename derived the same way
    # store does, so migration must find it too.
    lid_jid = "237786636488820:23@lid"
    (data_dir / "ctx" / f"{lid_jid.replace('/', '_')}.json").write_text(
        json.dumps([{"from": "y", "text": "hey", "ts": "2026-09-22T09:30:00"}]))
    return state_root, queue


def test_migration_produces_the_expected_layout():
    with tmpdir() as d:
        state_root, original_queue = _pre_migration_layout(d)
        changed = _migrate(state_root)
        assert changed is True

        data_dir = state_root / "state"
        accts = json.loads((data_dir / "accounts.json").read_text())
        assert accts == [{
            "id": "personal", "label": "Personal", "wuzapi_user": "omarchy",
            "agent": "respuesta.md", "quick_replies": ["Uno", "Dos"],
        }]

        migrated_queue = json.loads((data_dir / "queue.json").read_text())
        assert len(migrated_queue) == len(original_queue), "no entry may be lost"
        by_old_jid = {e["jid"]: e for e in migrated_queue}
        for original in original_queue:
            m = by_old_jid[original["jid"]]
            assert m["account"] == "personal"
            assert m["id"] == "personal:" + original["jid"]
            assert m["text"] == original["text"]
            assert m["status"] == original["status"]

        config = json.loads((data_dir / "config.json").read_text())
        assert config["groups_enabled"] == ["1@g.us"], "groups_enabled must stay put"
        assert "quick_replies" not in config, "quick_replies must move to the account"

        assert not list((data_dir / "ctx").glob("*.json")), "ctx must not be left flat"
        moved = sorted(p.name for p in (data_dir / "ctx" / "personal").glob("*.json"))
        assert moved == sorted([
            "1@s.whatsapp.net.json", "2@s.whatsapp.net.json",
            "237786636488820:23@lid.json",
        ])

        secrets_text = (state_root / "secrets.env").read_text()
        assert "WA_TOKEN=tok-personal-789" in secrets_text, "original token must survive"
        assert "WA_TOKEN_PERSONAL=tok-personal-789" in secrets_text


def test_migration_is_idempotent_running_twice_matches_running_once():
    with tmpdir() as d1, tmpdir() as d2:
        state_root_1, _ = _pre_migration_layout(d1)
        _migrate(state_root_1)

        state_root_2, _ = _pre_migration_layout(d2)
        _migrate(state_root_2)
        second_run_changed = _migrate(state_root_2)

        assert second_run_changed is False

        def _snapshot(root):
            data_dir = root / "state"
            return (
                json.loads((data_dir / "accounts.json").read_text()),
                json.loads((data_dir / "queue.json").read_text()),
                json.loads((data_dir / "config.json").read_text()),
                sorted(p.name for p in (data_dir / "ctx" / "personal").glob("*.json")),
                (root / "secrets.env").read_text(),
            )
        assert _snapshot(state_root_1) == _snapshot(state_root_2)


def test_migration_on_an_already_migrated_layout_is_a_complete_noop():
    with tmpdir() as d:
        state_root, _ = _pre_migration_layout(d)
        _migrate(state_root)

        data_dir = state_root / "state"
        before = {
            "accounts": (data_dir / "accounts.json").read_text(),
            "queue": (data_dir / "queue.json").read_text(),
            "config": (data_dir / "config.json").read_text(),
            "secrets": (state_root / "secrets.env").read_text(),
            "ctx": sorted(p.name for p in (data_dir / "ctx" / "personal").glob("*.json")),
        }
        changed = _migrate(state_root)
        assert changed is False
        after = {
            "accounts": (data_dir / "accounts.json").read_text(),
            "queue": (data_dir / "queue.json").read_text(),
            "config": (data_dir / "config.json").read_text(),
            "secrets": (state_root / "secrets.env").read_text(),
            "ctx": sorted(p.name for p in (data_dir / "ctx" / "personal").glob("*.json")),
        }
        assert before == after


def test_migration_retry_after_an_earlier_ctx_move_merges_instead_of_dropping():
    # C1: a crash between step 4 (move ctx) and the final accounts.json
    # write, followed by the still-legacy sink writing fresh messages to a
    # new flat ctx file for the same jid, must not lose either side when
    # the retry runs. Simulate exactly that partial state by hand.
    with tmpdir() as d:
        state_root, _ = _pre_migration_layout(d)
        data_dir = state_root / "state"
        ctx_dir = data_dir / "ctx"
        jid = "1@s.whatsapp.net"
        safe = jid.replace("/", "_")

        # First run's step 4 already moved this jid's ring...
        account_ctx_dir = ctx_dir / "personal"
        account_ctx_dir.mkdir(parents=True)
        (account_ctx_dir / f"{safe}.json").write_text(
            json.dumps([{"from": "x", "text": "hi", "ts": "2026-09-22T09:00:00"}]))
        # ...but never reached accounts.json, so a legacy write landed a
        # NEW flat file at the old path in the meantime.
        (ctx_dir / f"{safe}.json").write_text(
            json.dumps([{"from": "x", "text": "durante el corte",
                         "ts": "2026-09-22T09:45:00"}]))

        changed = _migrate(state_root)
        assert changed is True

        merged = json.loads((account_ctx_dir / f"{safe}.json").read_text())
        texts = [m["text"] for m in merged]
        assert "hi" in texts, "the already-migrated message must survive"
        assert "durante el corte" in texts, \
            "the message written during the interruption must survive"
        assert not (ctx_dir / f"{safe}.json").exists(), \
            "the flat file must not be left behind once merged"


def test_migration_retry_after_an_earlier_queue_rewrite_does_not_double_prefix():
    # I3: simulate a crash right after step 3 (queue rewrite + save) but
    # before ctx/secrets/accounts.json. The retry must not re-rewrite
    # already-migrated entries into "personal:personal:<jid>".
    with tmpdir() as d:
        state_root, original_queue = _pre_migration_layout(d)
        data_dir = state_root / "state"
        already_rewritten = []
        for e in original_queue:
            e = dict(e)
            e["account"] = "personal"
            e["id"] = store.entry_id("personal", e["id"])
            already_rewritten.append(e)
        (data_dir / "queue.json").write_text(
            json.dumps(already_rewritten, ensure_ascii=False))

        changed = _migrate(state_root)
        assert changed is True

        migrated_queue = json.loads((data_dir / "queue.json").read_text())
        assert len(migrated_queue) == len(original_queue), "no entry may be lost"
        by_jid = {e["jid"]: e for e in migrated_queue}
        for original in original_queue:
            m = by_jid[original["jid"]]
            assert m["id"] == "personal:" + original["jid"], \
                "an already-migrated id must not be prefixed a second time"
            assert m["text"] == original["text"]
            assert m["status"] == original["status"]


def test_migration_retry_before_the_config_cleanup_loses_nothing():
    # I3: simulate a crash after accounts.json was written (the commit
    # marker) but before the config.json quick_replies cleanup ran. The
    # gate means a retry is now a declared no-op — the point of this test
    # is that the leftover duplicate is harmless, not that it gets cleaned
    # up retroactively, and that nothing else regresses.
    with tmpdir() as d:
        state_root, original_queue = _pre_migration_layout(d)
        data_dir = state_root / "state"
        _migrate(state_root)

        # Put the quick_replies key back into config.json, as if the
        # cleanup step never ran.
        config = json.loads((data_dir / "config.json").read_text())
        config["quick_replies"] = ["Uno", "Dos"]
        (data_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False))

        changed = _migrate(state_root)
        assert changed is False, "accounts.json existing must still gate everything off"

        accts = json.loads((data_dir / "accounts.json").read_text())
        assert accts[0]["quick_replies"] == ["Uno", "Dos"], \
            "the account's own copy must be untouched by the leftover duplicate"
        migrated_queue = json.loads((data_dir / "queue.json").read_text())
        assert len(migrated_queue) == len(original_queue), "no entry may be lost"
        config_after = json.loads((data_dir / "config.json").read_text())
        assert config_after["groups_enabled"] == ["1@g.us"]


if __name__ == "__main__":
    run(dict(globals()))
