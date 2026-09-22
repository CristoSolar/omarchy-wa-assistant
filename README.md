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

After `install.sh` finishes:

```sh
./bin/wa-up
omarchy-restart-bar
```

`wa-up` creates `~/.local/share/omarchy-wa/`, generates a random admin
token, encryption key, and API token into `secrets.env` on first run, and
starts both containers (`omarchy-wuzapi` and `omarchy-wa-sink`) on a private
Docker network called `omarchy-wa`. Only `omarchy-wuzapi`'s port is
published, and only to `127.0.0.1`.

## Pairing

```sh
./bin/wa-pair <number, international format, no +>
```

This prints an 8-digit linking code and tells you where to enter it:
**WhatsApp > Linked devices > Link device > Link with phone number**. Once
WhatsApp reports the session as logged in, `wa-pair` does *not* stop there:
it watches the row count in wuzapi's history table until it stops growing,
restarts the sink container (which purges that table on startup), and then
checks that the table is actually empty. Expect this whole step to take a
minute or two after you enter the code — that's the history dump arriving
and being cleared, not a hang.

On success you'll see something like:

```
linked.
waiting for WhatsApp to finish sending history...
history purged: 14832 rows deleted, table now empty.
```

If instead you see:

```
WARNING: history may NOT have been fully cleared.
  History was still arriving when purge ran: settled=False
  Rows before restart: 9021, rows remaining: 342
  Please run 'docker restart omarchy-wa-sink' in a minute and check again.
```

do exactly that: wait a minute, run `docker restart omarchy-wa-sink`
yourself, and re-check. The sink purges the history table every time it
starts, so a second restart after the dump has actually finished should
leave the table empty. Don't consider pairing done until you've confirmed
that.

## Enabling a group

Group chats are ignored by default. To let one through, add its JID to
`groups_enabled` in `~/.local/share/omarchy-wa/config.json`:

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
yet prints one line naming the JID and the group's display name, for
example:

```
skipping messages from group not in groups_enabled: jid='120363012345678901@g.us' name='Familia'
```

It logs each JID only once per process, not once per message, so a busy
group won't flood the log — if you don't see the line, send another
message. Copy the JID into the array above.

## Turning off dry-run

Every send goes through `bin/wa-reply`, and by default it does nothing but
report what it *would* have sent — the panel will show "DRY RUN — no se
envió" and no request ever reaches wuzapi. This is the tool's default state
on every fresh install, not something you have to remember to enable.

To let it actually send messages to real people, add this line to
`~/.local/share/omarchy-wa/secrets.env`:

```
WA_DRY_RUN=0
```

Be clear-eyed about what this line does: it is the one thing that turns a
harmless drafting tool into something that messages real people on your
behalf, on a bridge that can get your number banned. Flip it only once
you're comfortable with both of those facts.

## The "unknown" send outcome

If a send times out or the connection drops mid-request, `wa-reply` cannot
tell whether wuzapi received it before the failure. Rather than guessing,
it reports the outcome as unknown, and the panel shows:

> sin confirmación: puede que el mensaje ya se haya enviado — revisá
> WhatsApp antes de reintentar

and disables the **Enviar** button for that entry. This is intentional:
retrying blind risks sending the same person the same message twice. Check
WhatsApp yourself to see whether it went out, then go back to the queue
(**Volver**) and re-select the entry — that's what re-arms the send button.

## Where state lives, and how to wipe it

Everything lives under `~/.local/share/omarchy-wa/` (mode 700):

- `secrets.env` — the admin token, encryption key, API token, and (if you
  set it) `WA_DRY_RUN=0`. Mode 600.
- `queue.json` — pending messages shown in the panel.
- `ctx/` — one small JSON file per chat, the last 10 messages of context
  used when drafting.
- `config.json` — `groups_enabled` and any other hand-edited settings.
- `wuzdata/` — wuzapi's own SQLite database and session data, bind-mounted
  into both containers. This is where the history dump lands before
  `wa-pair`/the sink purge it.

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
