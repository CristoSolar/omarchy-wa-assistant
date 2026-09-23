# sink/accounts.py
"""The accounts model: `state/accounts.json` and lookups over it.

An account is a dict with at least `id`, `label` and `wuzapi_user`; it may
also carry `agent` (the tone file under `agents/`) and `quick_replies` (its
own one-click replies). This module only reads and looks up — it never
writes accounts.json; that is store.migrate_single_account's job today, and
bin/wa-pair's job from Task 4 on.
"""
import re
from pathlib import Path

from sink import store

# An account id is a slug, and the charset is not cosmetic: the id is
# upper-cased to name that account's token variable
# (WA_TOKEN_<ID_UPPER>, derived in bin/wa-reply's token_env_var). Any
# charset that survives upper-casing non-injectively lets two DIFFERENT
# accounts — `trabajo` and `Trabajo`, which by_id treats as distinct —
# resolve to the SAME token, and a message then goes out under the wrong
# identity with nothing refusing it. Restricting ids to lowercase ASCII,
# digits and underscore makes `.upper()` injective, so distinct ids can
# never collapse onto one variable name. It also keeps the derived name a
# legal shell identifier, which matters because bin/wa-pair writes it into
# secrets.env and sources it.
# \A ... \Z, not ^...$: `$` also matches just before a trailing
# newline, so "trabajo\n" would pass and then name a variable with a
# newline in it.
ID_CHARSET = re.compile(r"\A[a-z0-9_]+\Z")


def validate_id(account_id):
    """Return the id if it is a legal account id; raise ValueError if not.

    THE single definition of what an account id may contain. Enforced at
    both ends so it cannot be bypassed: here on every read of
    accounts.json (a malformed id in the file is refused loudly rather
    than carried around) and in token_env_var (no caller can derive a
    credential's name for an id that was never validated).
    """
    if not isinstance(account_id, str) or not ID_CHARSET.match(account_id):
        raise ValueError(
            f"invalid account id {account_id!r}: an account id must match "
            f"{ID_CHARSET.pattern} (lowercase ASCII letters, digits and "
            f"underscore). Two ids differing only by case would share one "
            f"WA_TOKEN_<ID> variable and could send under the wrong number.")
    return account_id


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
    seen = set()
    for account in accounts:
        if not isinstance(account, dict):
            raise store.StoreReadError(
                path, f"expected each account to be an object, got "
                      f"{type(account).__name__}")
        try:
            account_id = validate_id(account.get("id"))
        except ValueError as exc:
            raise store.StoreReadError(path, str(exc)) from exc
        if account_id in seen:
            # First-wins would be silent and wrong: two entries with the
            # same id and different `agent` or `quick_replies` would give
            # one of them the other's tone and quick list, with nothing
            # said. A duplicate id is a broken accounts file.
            raise store.StoreReadError(
                path, f"duplicate account id {account_id!r}: every account "
                      f"must have its own id")
        seen.add(account_id)
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
