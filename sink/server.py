"""Receive wuzapi webhooks and write them to the queue. Never sends anything.

Binds 0.0.0.0 inside its own container. No port is published to the host, so
only wuzapi on the private Docker network can reach it.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from sink import store
from sink.events import describe_skipped_group, extract
from sink.purge import purge_history

PORT = 8099

# JIDs of not-yet-enabled groups already logged, so a busy group chat
# doesn't flood the log with the same JID on every message. Process-lifetime
# only — it does not need to survive a restart.
_logged_group_jids = set()


def _decode(raw: bytes, content_type: str):
    text = raw.decode("utf-8", "replace")
    if "form-urlencoded" in (content_type or ""):
        fields = urllib.parse.parse_qs(text)
        payload = fields.get("jsonData", [""])[0]
    else:
        payload = text
    try:
        return json.loads(payload)
    except ValueError:
        return None


def handle_payload(raw: bytes, content_type: str, data_dir) -> bool:
    event = _decode(raw, content_type)
    if event is None:
        return False
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
    store.append_context(data_dir, entry["jid"],
                         {"from": entry["push_name"], "text": entry["text"],
                          "ts": entry["ts"]})
    store.upsert_queue(data_dir, {
        "id": entry["jid"], "jid": entry["jid"],
        "push_name": entry["push_name"], "text": entry["text"],
        "count": 1, "ts": entry["ts"], "status": "pending",
    })
    return True


class Handler(BaseHTTPRequestHandler):
    data_dir = None

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            handle_payload(raw, self.headers.get("Content-Type", ""), self.data_dir)
        except Exception as exc:  # a bad event must never kill the sink
            print(f"error handling event: {exc}", file=sys.stderr, flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

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
