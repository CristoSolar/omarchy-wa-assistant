"""Receive wuzapi webhooks and write them to the queue. Never sends anything.

Binds 0.0.0.0 inside its own container. No port is published to the host, so
only wuzapi on the private Docker network can reach it.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from sink import accounts as accounts_module
from sink import store
from sink.contacts import display_name
from sink.events import describe_skipped_group, extract, extract_own_message, extract_read_receipt
from sink.purge import purge_history

PORT = 8099

# JIDs of not-yet-enabled groups already logged, so a busy group chat
# doesn't flood the log with the same JID on every message. Process-lifetime
# only — it does not need to survive a restart.
_logged_group_jids = set()

# instanceNames that matched no configured account, already logged, so a
# misconfigured or not-yet-paired wuzapi user doesn't flood the log with
# the same name on every message. Process-lifetime only, same as
# _logged_group_jids.
_logged_unknown_instances = set()


def _decode(raw: bytes, content_type: str):
    """Return (event, instance_name), or (None, None) if the body is unusable.

    Both webhook formats carry the account's wuzapi_user as `instanceName`
    alongside the event payload: form-urlencoded as a sibling field next to
    `jsonData`, JSON as a sibling key next to `event`. Reading it from the
    same envelope in both cases keeps `events.extract` (which only ever
    looks at the `event` key) unaware that accounts exist at all.
    """
    text = raw.decode("utf-8", "replace")
    if "form-urlencoded" in (content_type or ""):
        fields = urllib.parse.parse_qs(text)
        payload_text = fields.get("jsonData", [""])[0]
        instance_name = fields.get("instanceName", [""])[0]
        try:
            return json.loads(payload_text), instance_name
        except ValueError:
            return None, None
    try:
        event = json.loads(text)
    except ValueError:
        return None, None
    instance_name = event.get("instanceName") if isinstance(event, dict) else None
    return event, instance_name


def _log_shape(event, instance_name) -> None:
    """Diagnostics behind WA_DEBUG_EVENTS: the event's shape, never its
    content: type, state, chat/sender ids and the message's top-level keys,
    so a receipt or an unsupported message can be told apart without
    writing anyone's text to the log."""
    if not isinstance(event, dict):
        return
    inner = event.get("event") if isinstance(event.get("event"), dict) else {}
    info = inner.get("Info") if isinstance(inner.get("Info"), dict) else inner
    message = inner.get("Message") if isinstance(inner.get("Message"), dict) else {}
    print("event-shape", json.dumps({
        "instance": instance_name, "type": event.get("type"),
        "state": event.get("state"), "chat": info.get("Chat"),
        "sender": info.get("Sender"), "from_me": info.get("IsFromMe"),
        "msg_keys": sorted(message.keys()),
        "receipt_keys": sorted(inner.keys()) if event.get("type") == "ReadReceipt" else None,
    }), file=sys.stderr, flush=True)


def _log_unknown_instance_once(instance_name, jid) -> None:
    if instance_name not in _logged_unknown_instances:
        _logged_unknown_instances.add(instance_name)
        print(f"skipping message from unrecognised instanceName "
             f"(not in accounts.json): instance={instance_name!r} "
             f"jid={jid!r}",
             file=sys.stderr, flush=True)


def _remove_unless_ambiguous(data_dir, account_id, jid) -> bool:
    """Remove that chat's queue entry, unless its status is ambiguous.

    An entry left `unknown` or `sending` may correspond to a send that
    reached the recipient without confirmation reaching us; it is the only
    record of that ambiguity, so neither a read receipt nor an own-device
    message may silently clear it. Returns True only if an entry actually
    was removed, so callers can tell "cleared" from "nothing to clear" and
    from "kept on purpose".
    """
    entry_id = store.entry_id(account_id, jid)
    for e in store.load_queue(data_dir):
        if e["id"] == entry_id:
            if e.get("status") in ("unknown", "sending"):
                return False
            store.remove_queue(data_dir, entry_id)
            return True
    return False


def _handle_read_receipt(event, instance_name, data_dir) -> bool:
    receipt = extract_read_receipt(event)
    if receipt is None:
        return False
    accounts = accounts_module.load(data_dir)
    account = accounts_module.by_instance(accounts, instance_name)
    if account is None:
        _log_unknown_instance_once(instance_name, receipt["jid"])
        return False
    return _remove_unless_ambiguous(data_dir, account["id"], receipt["jid"])


def _handle_own_message(own, instance_name, data_dir) -> bool:
    accounts = accounts_module.load(data_dir)
    account = accounts_module.by_instance(accounts, instance_name)
    if account is None:
        _log_unknown_instance_once(instance_name, own["jid"])
        return False

    account_id = account["id"]
    jid = own["jid"]
    # The sink is the sole writer of outgoing ring entries. WhatsApp echoes
    # every outgoing message to linked devices, including a send made
    # through the plugin (bin/wa-reply), so this one write covers both "the
    # owner replied from another device" and "the plugin just sent this" —
    # bin/wa-reply itself no longer writes to the ring at all. That removes
    # the two-writer race this event used to need an echo heuristic to
    # paper over: there is now exactly one writer, so there is nothing left
    # to de-duplicate against, and two genuinely identical own messages
    # sent back to back are both recorded rather than one being mistaken
    # for the other's echo.
    store.append_context(data_dir, jid,
                         {"direction": "out", "text": own["text"],
                          "ts": own["ts"]}, account_id)
    _remove_unless_ambiguous(data_dir, account_id, jid)
    return True


def handle_payload(raw: bytes, content_type: str, data_dir) -> bool:
    event, instance_name = _decode(raw, content_type)
    if event is None:
        return False
    if os.environ.get("WA_DEBUG_EVENTS"):
        _log_shape(event, instance_name)

    if isinstance(event, dict) and event.get("type") == "ReadReceipt":
        return _handle_read_receipt(event, instance_name, data_dir)

    own = extract_own_message(event)
    if own is not None:
        return _handle_own_message(own, instance_name, data_dir)

    config = store.load_config(data_dir)
    groups_enabled = config.get("groups_enabled", [])
    entry = extract(event, groups_enabled)
    if entry is None:
        skipped = describe_skipped_group(event, groups_enabled)
        if skipped and skipped["jid"] not in _logged_group_jids:
            _logged_group_jids.add(skipped["jid"])
            print(f"skipping messages from group not in groups_enabled: "
                 f"jid={skipped['jid']!r} sender={skipped['sender']!r}",
                 file=sys.stderr, flush=True)
        return False

    # accounts.load raises StoreReadError for a broken accounts.json (exists
    # but unreadable or unparsable). We deliberately do NOT catch it here:
    # this sits on the path that decides whose context a message lands in,
    # and a broken accounts file must never be treated as "no accounts
    # configured" (which would fall through to "unknown instanceName" below
    # and drop the message with a misleading log line) nor guessed past
    # (which would risk filing a message under the wrong account). Letting
    # it propagate means Handler.do_POST's existing catch-all logs the real
    # reason loudly and the sink stays up — the event is still dropped, but
    # with an honest error instead of a fabricated "unknown account".
    accounts = accounts_module.load(data_dir)
    account = accounts_module.by_instance(accounts, instance_name)
    if account is None:
        if instance_name not in _logged_unknown_instances:
            _logged_unknown_instances.add(instance_name)
            print(f"skipping message from unrecognised instanceName "
                 f"(not in accounts.json): instance={instance_name!r} "
                 f"jid={entry['jid']!r} sender={entry['push_name']!r}",
                 file=sys.stderr, flush=True)
        return False

    account_id = account["id"]
    wuzdata = os.path.dirname(os.environ.get("WA_WUZAPI_DB", "/wuzdata/users.db"))
    entry["push_name"] = display_name(wuzdata, instance_name, entry["jid"], entry["push_name"])
    # "direction": "in" is an explicit mark, not something a reader is meant
    # to infer from "from" — a contact can share the user's own display name,
    # and the user's own second number writes to them too. The "out" half
    # comes from _handle_own_message, via WhatsApp's echo of every send.
    store.append_context(data_dir, entry["jid"],
                         {"direction": "in", "from": entry["push_name"],
                          "text": entry["text"], "ts": entry["ts"]}, account_id)
    queue_entry = {
        "id": store.entry_id(account_id, entry["jid"]), "jid": entry["jid"],
        "account": account_id,
        "push_name": entry["push_name"], "text": entry["text"],
        "count": 1, "ts": entry["ts"], "status": "pending",
    }
    if "attachment" in entry:
        queue_entry["attachment"] = entry["attachment"]
    store.upsert_queue(data_dir, queue_entry)
    return True


def response_status(raw: bytes, content_type: str, data_dir) -> int:
    """Process one event and return the HTTP status to answer wuzapi with.

    wuzapi treats 200 as "delivered" and drops the event; anything else is
    retried (five attempts, thirty seconds apart per the design spec). So
    the two failure shapes must get different answers:

    - A deliberate decision not to queue — the user's own message, a group
      not in groups_enabled, an unrecognised instanceName, or a body that
      doesn't parse as an event at all — is correct and final. Retrying it
      would change nothing, so it answers 200 whether handle_payload
      returned True or False.
    - A processing failure — StoreReadError from a broken accounts.json or
      queue file, a disk write that fails, or any other unexpected
      exception — means the event was never actually handled. It answers
      500 so wuzapi's retry window is what actually recovers it, instead
      of the event silently vanishing behind a log line nobody is
      watching. It is still logged loudly here, and the exception is
      still swallowed: a bad event must never kill the sink.
    """
    try:
        handle_payload(raw, content_type, data_dir)
    except Exception as exc:  # a bad event must never kill the sink
        print(f"error handling event: {exc}", file=sys.stderr, flush=True)
        return 500
    return 200


class Handler(BaseHTTPRequestHandler):
    data_dir = None

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        status = response_status(raw, self.headers.get("Content-Type", ""), self.data_dir)
        self.send_response(status)
        self.end_headers()
        self.wfile.write(b"ok" if status == 200 else b"error")

    def log_message(self, *args):
        pass


def purge_or_warn(db_path) -> int:
    """Purge wuzapi's history table, but never let a corrupt DB stop the sink.

    The purge is the only thing keeping the user's WhatsApp history off disk.
    If it fails we must not silently continue as if it worked: we shout a
    loud warning to stderr so the failure is visible, then let the sink keep
    serving anyway (staying up beats a crash-loop with no webhook sink at
    all).
    """
    try:
        return purge_history(db_path)
    except Exception as exc:
        print("!" * 70, file=sys.stderr)
        print("WARNING: history purge FAILED — wuzapi DB may still hold "
              "the user's WhatsApp message history unencrypted on disk.",
              file=sys.stderr)
        print(f"WARNING: purge_history({db_path!r}) raised: {exc}",
              file=sys.stderr)
        print("!" * 70, file=sys.stderr, flush=True)
        return 0


def main():
    Handler.data_dir = store.data_dir()
    wuz_db = os.environ.get("WA_WUZAPI_DB", "/wuzdata/users.db")
    print(f"purged {purge_or_warn(wuz_db)} history rows", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
