# sink/accounts.py
"""The accounts model: `state/accounts.json` and lookups over it.

An account is a dict with at least `id`, `label` and `wuzapi_user`; it may
also carry `agent` (the tone file under `agents/`) and `quick_replies` (its
own one-click replies). This module only reads and looks up — it never
writes accounts.json; that is store.migrate_single_account's job today, and
bin/wa-pair's job from Task 4 on.
"""
from pathlib import Path

from sink import store


def load(data_dir) -> list:
    """Read `accounts.json` from the sink-visible state directory.

    A missing file reads as an empty list — a fresh, unmigrated or
    not-yet-configured install has no accounts yet. A file that exists but
    cannot be parsed, or parses to something other than a JSON list, still
    raises store.StoreReadError, same as every other state file: never
    mistake a broken accounts.json for "no accounts".
    """
    path = Path(data_dir) / "accounts.json"
    accounts = store.read_json(path, [])
    if not isinstance(accounts, list):
        raise store.StoreReadError(
            path, f"expected a JSON list, got {type(accounts).__name__}")
    return accounts


def by_instance(accounts, instance_name):
    """The account whose `wuzapi_user` matches the webhook's `instanceName`."""
    for account in accounts:
        if account.get("wuzapi_user") == instance_name:
            return account
    return None


def by_id(accounts, account_id):
    """The account whose `id` matches a queue entry's `account` field."""
    for account in accounts:
        if account.get("id") == account_id:
            return account
    return None
