# WhatsApp Assistant

*[Leer en español](README.es.md)*

An Omarchy bar widget that queues incoming WhatsApp messages and drafts
replies with Claude. You give the idea in one line ("tell them I'm running
late"), Claude writes the message in the chat's own context, and nothing is
sent until you press **Enviar** — the panel starts every install in a
dry-run mode that never touches the network.

## Read this before you pair anything

The bridge that talks to WhatsApp is [wuzapi](https://github.com/asternic/wuzapi),
built on [whatsmeow](https://github.com/tulir/whatsmeow). It is **not** the
official WhatsApp Business API — it logs in as if it were a second phone
linked to your account, the same way WhatsApp Web does, but without
WhatsApp's blessing. The wuzapi project itself says plainly that using it
can get your number banned. That risk is real, it is not hypothetical, and
this README is not going to soften it: if you pair your main number, you are
choosing to accept that WhatsApp may lock you out of it. Consider a spare
number for anything but casual, low-volume use.

There's a second thing to know before you pair: linking a device makes
WhatsApp dump the account's *entire* message history into wuzapi's local
SQLite database, unencrypted. In an earlier test that was on the order of
15,000 messages. The setting that looks like it should stop this,
`days_to_sync_history`, does not — set to `0` it means "WhatsApp's default,"
not "none." `wa-pair` handles this for you (see "Pairing" below), but you
should understand that it's happening and why it takes a minute.

## What it does, in short

```
WhatsApp ──> wuzapi (Docker, loopback only) ──webhook──> sink (Docker, private network)
                                                              │ writes queue.json, per-chat
                                                              │ context, purges history
                                                              ▼
                                              Bar widget ──> Panel: idea → claude -p → draft → Enviar
```

- **wuzapi** is the only piece that speaks to WhatsApp. It's published on
  `127.0.0.1:8080` only — nothing outside your machine can reach it.
- **The sink** (`sink/server.py`) receives wuzapi's webhooks, drops
  anything sent by you, drops group messages unless the group is explicitly
  enabled, and writes everything else to a queue and a short per-chat
  context ring (last 10 messages). It never sends anything.
- **`bin/wa-reply`** is the only component that can send a message. It
  refuses an empty draft, refuses any recipient that isn't a full JID, and
  refuses to send at all unless `WA_DRY_RUN=0` is set.
- **The bar widget and panel** (`BarWidget.qml`, `Panel.qml`) show pending
  chats, let you type the idea, call `claude -p` to draft a reply against
  the chat's own agent instructions (`agents/respuesta.md`), and send it.
  There is one text box: type the idea and press **Redactar** (or Enter)
  to draft it, then the same box switches to holding the drafted message
  — editable, with the idea it came from shown above it — and only then
  does **Enviar** turn on.

## Requirements

`docker`, `curl`, `python3`, and `claude` (Claude Code) on your `PATH`.

## Install

```sh
git clone <this repo>
cd omarchy-wa-assistant
./install.sh
```

`install.sh` symlinks the plugin into
`~/.config/omarchy/plugins/io.github.cristosolar.waassistant`, and builds
**two** Docker images:

1. `omarchy-wa-sink`, built from this repo's own `docker/sink.Dockerfile`.
2. `omarchy-wuzapi`, built by cloning `asternic/wuzapi` from GitHub and
   running `docker build` on it — only if an image with that name doesn't
   already exist.

This is a deliberate one-time cost paid by `install.sh`, not by every-day
use. `bin/wa-up` (the command you run to bring the bridge up) does **not**
build the wuzapi image itself: if it's missing, `wa-up` prints the two
commands above and exits 1. Auto-cloning and building a third-party image
from the internet on a routine `wa-up` would be a slow, surprising side
effect to have on a command you run often; building it once, during
install, is not.

`install.sh` also puts the plugin's widget on the bar (via `omarchy-bar put`,
which is safe to run again — it leaves the widget where it is if it's
already there). After `install.sh` finishes:

```sh
./bin/wa-up
omarchy-restart-shell
```

`wa-up` creates `~/.local/share/omarchy-wa/`, generates a random admin
token, encryption key, and API token into `secrets.env` on first run, and
starts both containers (`omarchy-wuzapi` and `omarchy-wa-sink`) on a private
Docker network called `omarchy-wa`. Only `omarchy-wuzapi`'s port is
published, and only to `127.0.0.1`.

## Surviving restarts

Both containers are started with `--restart unless-stopped`, so a crash
brings them back on their own, and Docker's restart handling and the
`omarchy-wa` network's own name resolution converge regardless of which
container comes back first — `omarchy-wuzapi` and `omarchy-wa-sink` don't
need to start in any particular order to find each other by name.

That covers the containers coming back up. On its own it does **not** cover
a `docker`-level restart of `omarchy-wuzapi` staying able to *deliver*
messages: wuzapi keeps its webhook registration in memory, and a restart
(crash, OOM, reboot) drops it even though `GET /webhook` still shows it
set in the database.

### Automatic healing: `wa-heal`

`install.sh` installs and enables a systemd **user** timer,
`wa-heal.timer`, that runs `./bin/wa-heal` every 5 minutes and once at
login. `wa-heal` re-asserts the webhook when a session is logged in, then
reads it back with `GET /webhook` to confirm it actually stuck, instead of
trusting the `POST`'s 200. It is a no-op if nothing is paired yet or no
session is logged in, and safe to run as often as the timer likes.

This is what makes `--restart unless-stopped` on `omarchy-wuzapi` safe:
without it, a container that comes back from a crash or a reboot looks
fully healthy — containers up, `wa-status` reporting
`ok: true, logged_in: true`, the bar pill green — while silently receiving
nothing, because nothing had re-registered the webhook. `wa-heal` closes
that gap on its own, on a timer, without anyone needing to notice the
stack looked fine but wasn't and run `wa-up` by hand. `wa-up` itself
still calls the same `wa-heal` script (not a second copy of the logic)
right after bringing the containers up, so a fresh `wa-up` still gives you
an immediate answer.

**Check it's working:**

```sh
systemctl --user status wa-heal.timer      # is the timer scheduled?
journalctl --user -u wa-heal.service -n 20 # what did the last few runs report?
```

A healthy run logs `wa-heal: webhook healthy (...)` or a "nothing to
heal" no-op. A failed re-assert logs a loud `wa-heal: FAILED to
re-assert the webhook` to the unit's stderr (captured by journald) and
the service exits non-zero, which `systemctl --user status` will show as
a failed last run.

**Turn it off:**

```sh
systemctl --user disable --now wa-heal.timer
```

You can still run `./bin/wa-heal` by hand at any time, or fall back to
running `./bin/wa-up` after a restart, as before.

`--restart unless-stopped` (not `always`) is deliberate: this stack holds
a live link to your personal WhatsApp session, so a `docker stop` you did
on purpose must stay stopped, not resurrect itself on the next boot.
Re-examined now that `wa-heal` exists: `unless-stopped` is still the
right call, and arguably more so — the container actually coming back up
is a precondition for `wa-heal` being able to heal anything at all; a
policy that left it down would mean the timer has nothing to re-assert
against.

**`--restart unless-stopped` alone will not bring the stack back after a
reboot** unless the Docker daemon itself is set to start at boot. That is
a system-level change, made with `sudo`, outside this repo:

```sh
sudo systemctl enable docker
```

Be aware that enabling the Docker service this way starts **every**
container on the machine that carries a restart policy, not just this
plugin's two — that's a consequence of enabling the daemon itself, not
something this plugin does.

## Pairing

```sh
./bin/wa-pair <number, international format, no +>
```

This prints an 8-digit linking code and tells you where to enter it:
**WhatsApp > Linked devices > Link device > Link with phone number**. Once
WhatsApp reports the session as logged in, `wa-pair` does *not* stop there:
it watches the row count in wuzapi's history table until rows have
actually appeared and then stopped growing, restarts the sink container
(which purges that table on startup), polls until the purge finishes, and
then checks that the table is really empty. Expect this whole step to take
a couple of minutes after you enter the code — that's the history dump
arriving and being cleared, not a hang.

There are exactly three things it can tell you, and they mean different
things.

**1. It worked.**

```
linked.
waiting for WhatsApp to finish sending history...
history purged: 14832 rows deleted, table now empty.
```

**2. It did not work.**

```
WARNING: history may NOT have been fully cleared.
  History was still arriving when purge ran: settled=False
  Rows before restart: 9021, rows remaining: 342
  Please run 'docker restart omarchy-wa-sink' in a minute and check again.
```

Do exactly that: wait a minute, run `docker restart omarchy-wa-sink`
yourself, and re-check. The sink purges the history table every time it
starts, so a second restart after the dump has actually finished should
leave the table empty. Don't consider pairing done until you've confirmed
that.

**3. It cannot tell.**

```
history: no rows were ever observed in wuzapi's message_history table.
  That may mean this account has no history to sync, or it may mean
  WhatsApp had not started sending it yet. This run cannot tell which.
  Nothing was confirmed purged, because nothing was ever seen.
```

This is **not** a success message, even though the command exits 0.
`loggedIn` flips at the end of the pairing handshake, *before* whatsmeow
starts streaming history, so an empty table is genuinely ambiguous and
nothing in the database can resolve it. If you want certainty, wait a few
minutes and then:

```sh
docker restart omarchy-wa-sink
docker logs omarchy-wa-sink   # look for the "purged N history rows" line
```

## Enabling a group

Group chats are ignored by default. To let one through, add its JID to
`groups_enabled` in `~/.local/share/omarchy-wa/state/config.json`:

```json
{"groups_enabled": ["120363012345678901@g.us"]}
```

Changes take effect immediately — the sink re-reads `config.json` on every
incoming message, so there's no need to restart anything after editing it.

This is a hand-edited file in this MVP, so you need the group's JID before
you can enable it. To find it: have someone post in the group (or wait for
the next message there), then check the sink's log:

```sh
docker logs omarchy-wa-sink
```

Every group message it drops because the group isn't in `groups_enabled`
yet prints one line, for example:

```
skipping messages from group not in groups_enabled: jid='120363012345678901@g.us' sender='Familia'
```

The JID is what you copy into `groups_enabled` — that part is stable.
`sender` is **not** the group's name or subject; there's no such field
available here. It's the display name of whoever happened to post the
message that triggered the log line, so it will change from one message to
the next as different people post. Use it only as a rough clue about which
group you're looking at, never as a stable title, and rely on the JID to
tell two groups apart.

It logs each JID only once per process, not once per message, so a busy
group won't flood the log — if you don't see the line, send another
message. Copy the JID into the array above.

## Quick replies

The panel's chat detail view shows a row of quick-reply buttons above the
composer. **Clicking one sends that exact text immediately — there is no
confirmation step, no undo, and no draft to review first.** It is not a
shortcut for filling the composer; the message goes out on that click.
They're disabled while a draft or a send is already in progress, and an
entry whose last send is unconfirmed (`unknown`/`sending`) still requires
you to acknowledge that before a quick reply — same as `Enviar` — because
the guard exists on the `wa-reply send` side, not in the button.

Quick replies live in `quick_replies` in the same
`~/.local/share/omarchy-wa/state/config.json` as `groups_enabled`, as a
plain list of strings:

```json
{"groups_enabled": [], "quick_replies": [
  "Hola, soy el asistente AI de Cristóbal, en estos momentos no puede responder pero responderá en breve.",
  "Gracias, te respondo apenas pueda."
]}
```

`wa-up` seeds this key with one default entry the first time it finds the
file missing or lacking `quick_replies` at all; it never overwrites or
reorders a list that's already there, and never touches `groups_enabled`.

To add, edit or remove a quick reply: edit the `quick_replies` array by
hand (add a string, change one, or delete one) and save. There's no
restart needed — the panel re-reads it from `wa-reply quick` every time
you reopen a chat in the panel.

## Container logs and message content

`bin/wa-up` starts wuzapi with `LOG_LEVEL=warn`. Left unset, wuzapi defaults
to its most verbose level and writes the full text of every message —
yours and the other party's — into `docker logs omarchy-wuzapi` in
plaintext. That directly undermines the history purge above: there is
little point deleting 15,000 rows from SQLite if the same content is
sitting in the Docker journal instead. `warn` keeps connection problems
visible (failed logins, dropped sessions, the "No webhook set for user"
warning) while dropping the per-message debug lines that contain content.

The sink's own log (`docker logs omarchy-wa-sink`) is unaffected by this
setting and stays as described above: it logs only JIDs and display names
for group-skip decisions, never message bodies.

If you want to look at recent wuzapi activity without wading through
history, use `--since`:

```sh
docker logs --since 10m omarchy-wuzapi
```

If earlier DEBUG-level runs already wrote message content into the
journal and you want that gone, the log is tied to the container, not a
separate file you can selectively edit — remove it with the container:

```sh
docker rm -f omarchy-wuzapi
```

`wa-up` recreates it on the next run with logging already set to `warn`.

## Turning off dry-run

Every send goes through `bin/wa-reply`, and by default it does nothing but
report what it *would* have sent. No request ever reaches wuzapi, and
nothing in the queue changes: the panel shows "DRY RUN — no se envió nada.
La entrada sigue pendiente en la cola.", the entry stays open, and its
stored status is left exactly as it was — including an `unknown` or
`sending` guard, which a dry run must never clear — so you can keep
iterating on the draft. This is the
tool's default state on every fresh install, not something you have to
remember to enable.

To let it actually send messages to real people, add this line to
`~/.local/share/omarchy-wa/secrets.env`:

```
WA_DRY_RUN=0
```

Be clear-eyed about what this line does: it is the one thing that turns a
harmless drafting tool into something that messages real people on your
behalf, on a bridge that can get your number banned. Flip it only once
you're comfortable with both of those facts.

## Sends whose outcome nobody knows

Two things can leave an entry in a state where the message may already have
reached WhatsApp and nothing recorded the answer:

- **`unknown`** — the POST timed out or the connection dropped mid-request.
- **`sending`** — `wa-reply` was killed between starting the POST and
  learning its result: a bar restart, a logout, an OOM kill. The request
  can take up to 20 seconds, so this window is real.

Both get the same treatment, because both have the same consequence.
`wa-reply` records the status on that entry in `queue.json` and the panel
shows (wording adjusted for which of the two it was):

> sin confirmación: puede que el mensaje ya se haya enviado — revisá
> WhatsApp antes de reintentar

That record is durable, and it is what both halves of the protection read:

- `bin/wa-reply send` **refuses** an entry whose stored status is
  `unknown` or `sending` and returns
  `{"ok": false, "needs_ack": true, "status": "...", ...}` unless it is
  called with `--acknowledged`.
- The panel marks the entry in the queue list itself ("⚠ sin confirmar" or
  "⚠ envío interrumpido"), not just once you open it, and keeps **Enviar**
  disabled until you press *"Revisé WhatsApp, quiero reenviar"*. Pressing
  that is what passes `--acknowledged`.
- A new incoming message in the same chat does **not** clear either status.

Closing the popup, restarting the bar, letting the queue refresh, or doing
a dry run does **not** clear the block — the warning is recomputed from
what is on disk every time, so it cannot be lost by reopening the panel or
by pressing Enviar while dry-run is still on. Check WhatsApp yourself to
see whether the message went out before you acknowledge.

## A message that arrives while you're drafting

If someone writes again in the same chat between the moment you open an
entry and the moment the send is confirmed, the entry is **not** removed —
it stays in the queue carrying the newer message, and the panel says so:

> enviado. Llegó un mensaje nuevo en este chat mientras redactabas, así que
> la entrada sigue en la cola.

Your reply did go out. The queue is just not throwing away the thing you
haven't read yet.

## Where state lives, and how to wipe it

Everything lives under `~/.local/share/omarchy-wa/` (mode 700):

```
~/.local/share/omarchy-wa/
├── secrets.env     mode 600 — mounted into NO container
├── state/          mounted into the sink at /state
│   ├── queue.json
│   ├── ctx/
│   └── config.json
└── wuzdata/        mounted into both containers
```

- `secrets.env` — the admin token, encryption key, API token, and (if you
  set it) `WA_DRY_RUN=0`. Mode 600. It sits **outside** `state/` on
  purpose: the sink is the container that receives whatever arrives from
  the internet, and it has no business holding the credential that can
  send messages as you. It is not bind-mounted anywhere.
- `state/queue.json` — pending messages shown in the panel.
- `state/ctx/` — one small JSON file per chat, the last 10 messages of
  context used when drafting.
- `state/config.json` — `groups_enabled`, `quick_replies`, and any other
  hand-edited settings.
- `wuzdata/` — wuzapi's own SQLite database and session data, bind-mounted
  into both containers. This is where the history dump lands before
  `wa-pair`/the sink purge it.

Three environment variables override these independently, which is the
point of splitting them: `WA_STATE_ROOT` (the root above), `WA_DATA_DIR`
(the directory mounted into the sink, default `$WA_STATE_ROOT/state`) and
`WA_SECRETS_FILE` (default `$WA_STATE_ROOT/secrets.env`). The secrets path
is never derived from `WA_DATA_DIR`.

If you installed an earlier version, `wa-up` moves `queue.json`,
`config.json` and `ctx/` down into `state/` for you on the next run.

The sink runs as **your** user (`--user "$(id -u):$(id -g)"`), not root, so
whatever it writes under `state/` is readable by you. Earlier versions ran
the sink as root, which left `queue.json` and friends root-owned and
unreadable by you — the queue was full but `wa-reply list`, `wa-status`
and the panel all reported it as empty, because a permission error and an
empty queue looked identical. `wa-up` now detects state files it can't
read as your user and repairs their ownership automatically before
(re)starting anything; if it can't (e.g. Docker itself isn't reachable),
it prints the exact command to run by hand and refuses to start rather
than leave a broken install running. If you ever see this again, unreadable
or corrupt state now says so loudly instead of quietly reporting zero:
`wa-reply list` prints `{"error": "..."}` (never a bare `[]`) and exits
non-zero, `wa-status` reports `ok: false` (which the bar widget shows as
`WA ✕`), and the panel shows a red banner instead of an empty list.

To wipe everything and start clean:

```sh
docker rm -f omarchy-wuzapi omarchy-wa-sink
rm -rf ~/.local/share/omarchy-wa
```

## Unlinking

Do this in order:

1. On your phone: **WhatsApp > Linked devices**, and remove this device
   from the list. Do this first — it's the step that actually tells
   WhatsApp the link is over.
2. `docker rm -f omarchy-wuzapi omarchy-wa-sink`
3. `rm -rf ~/.local/share/omarchy-wa`

Skipping step 1 leaves the device listed as linked on WhatsApp's side even
after you've torn down the containers here.

## License

MIT
