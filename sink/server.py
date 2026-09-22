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
from sink.events import extract
from sink.purge import purge_history

PORT = 8099


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
    entry = extract(event, config.get("groups_enabled", []))
    if entry is None:
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


def main():
    Handler.data_dir = store.data_dir()
    wuz_db = os.environ.get("WA_WUZAPI_DB", "/wuzdata/users.db")
    print(f"purged {purge_history(wuz_db)} history rows", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
