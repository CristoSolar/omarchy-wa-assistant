# WhatsApp Assistant

*[Leer en español](README.es.md)*

An Omarchy bar widget and chat window for WhatsApp: it shows your chats and
their full history, and drafts replies with Claude. You give the idea in one line ("tell them I'm running
late"), Claude writes the message in the chat's own context, and nothing is
sent until you press Enter (or click a quick reply) — the plugin starts every install in a
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

This applies per number you link, not once per install. Pairing a second
account (`wa-pair <account-id> <number>`, see "Pairing" below) links a
second phone number the same way, through the same bridge, under the same
risk — it does not make the first number any safer, and it doubles the
number of accounts that can be banned. If that second number is a work
number, the calculus is worse, not the same: losing a personal number is an
inconvenience, losing the number your employer or your clients call you on
is not. Decide account by account whether the convenience is worth it, with
this in mind for each one you add — the tool will let you pair as many
numbers as you want, but it will not tell you which ones are worth the risk.

There's a second thing to know before you pair, and it changed: this plugin
now **keeps** your WhatsApp history. wuzapi stores every message it sees in
its local SQLite database (`users.db`), and the chat window reads from it.
Earlier versions purged that table on purpose; this one does the opposite
(the owner's decision, made knowing the cost). **That history is stored on
disk unencrypted**, for every account you link, including what third parties
wrote to you, up to 100,000 messages per account. If you don't want that,
this plugin is not for you. History starts from the moment this was turned
on: older messages are not there (see "What is not in v1" below).

## What it does, in short

```
WhatsApp ──> wuzapi (Docker, loopback only) ──webhook──> sink (Docker, private network)
                                                              │ writes queue.json, per-chat
                                                              │ context, pokes state/changed
                                                              ▼
                              Bar widget ──> Chat window: read history, reply, or idea → claude -p → draft
```

- **wuzapi** is the only piece that speaks to WhatsApp. It's published on
  `127.0.0.1:8080` only — nothing outside your machine can reach it.
- **The sink** (`sink/server.py`) receives wuzapi's webhooks, drops group
  messages unless the group is explicitly enabled, and writes everything
  else to a queue and a short per-chat context ring (last 10 messages).
  What you send yourself — from the phone, from WhatsApp Web, or through
  this plugin — never enters the queue; it goes into the ring marked as
  yours, and it takes that chat out of the queue, as does reading the chat
  on another device (see "What takes a chat out of the queue" below). It
  never sends anything, and it never downloads anything.
- **`bin/wa-reply`** is the only component that can send a message. It
  refuses an empty draft, refuses any recipient that isn't a full JID, and
  refuses to send at all unless `WA_DRY_RUN=0` is set. **A reply always
  leaves by the number it arrived on.** The account is read from the queue
  entry itself (written by the sink from the webhook's `instanceName` when
  the message came in) — there is no flag, environment variable or setting
  anywhere that can send a reply from a different account than the one that
  received it, and an entry naming an account no longer in `accounts.json`
  is refused rather than sent under some other one. This is a deliberate
  guarantee, not a limitation: answering a client from your personal number
  because something defaulted wrong is exactly the failure this closes by
  construction.
- **The bar widget and chat window** (`BarWidget.qml`, `ChatWindow.qml`) show
  your chats and let you type the idea, call `claude -p` to draft a reply against
  that chat's own account's tone file (see "Multiple accounts" below), and
  send it. See "The chat window" below for how it works.
  Every chat names the account it belongs to, and the conversation view
  states plainly which number a reply will go out from.
- **`bin/wa-media`** downloads an attachment when you ask to see it, with
  the token of the account the message arrived on, into a local cache —
  **unencrypted**, see "Attachments" below. It has no send path.
- **The gear (⚙)** opens a settings screen embedded in the chat window: the
  list of configured accounts, linking a new one, unlinking one, editing
  each account's tone and quick replies, and emptying the attachment
  cache — see "Settings screen" below.

## The chat window

Open it with **SUPER+ALT+W** or by clicking the counter in the bar (which
opens it on **No leídos**). It is an overlay window with a chat list on the
left (search box, and the tabs **Todos / No leídos / Trabajo / Personal**)
and the conversation on the right.

The shortcut lives in your own `~/.config/hypr/bindings.lua`; `install.sh`
does not add it. Add:

```lua
o.bind("SUPER + ALT + W", "WhatsApp", "omarchy-shell shell toggle io.github.cristosolar.waassistant '{}'")
```

In a conversation:

- History pages in as you scroll up; photos, voice notes and documents work
  as described under "Attachments".
- **Enter sends** what you typed — one click, no confirmation step, still
  subject to dry-run (see "Turning off dry-run").
- **Ctrl+Enter drafts with Claude**: what you typed is taken as the idea, and
  the draft lands in the box for you to edit. Drafting never sends.
- Quick-reply chips sit above the box; "Sale por: <cuenta>" says which number
  the message will leave from, always the one the chat belongs to.
- A strip appears when the bridge is offline. The gear opens settings.

**When a chat is marked read.** Only when you click it in the list: that
sends the read receipt to the phone. Chats the window restores or remembers
from last time, and chats opened by clicking a notification, are shown but
**not** marked read. Read markers are kept in `state/read.json`.

If a send's outcome is unknown (timeout, connection dropped, or the window
closed while the send was in flight), the window refuses to send again in
that chat until you confirm you checked WhatsApp; see "Sends whose outcome
nobody knows". A send wuzapi refused outright (the bridge is down) shows
"No salió" and can simply be retried.

## Notifications

`install.sh` installs and enables `systemd/wa-notify.service`, a user
service (`bin/wa-notify`) that raises a desktop notification per incoming
message — one per chat, replaced by the next message in it. Clicking it opens
that chat in the window (without marking it read). There is no notification
for your own messages, for groups you have not enabled unless they mention
you, or for the chat that is open and focused in the window. The service
only reads; it never sends.

## What is not in v1

- **Messages older than the moment history was turned on.** Asking the
  phone for older history did not work. The workaround is to unlink and
  re-pair the account with `days_to_sync_history` set, which makes WhatsApp
  dump what it has again.
- Sending attachments, reactions, editing or deleting messages.

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

Cloning and building wuzapi is a deliberate one-time cost paid by
`install.sh`, not by every-day use. `bin/wa-up` (the command you run to
bring the bridge up) does **not** build the wuzapi image itself: if it's
missing, `wa-up` prints the two commands above and exits 1. Auto-cloning
and building a third-party image from the internet on a routine `wa-up`
would be a slow, surprising side effect to have on a command you run
often; building it once, during install, is not.

The sink image is different: **`wa-up` rebuilds `omarchy-wa-sink` from
this repo on every run**, unconditionally. It's built from local source,
so Docker's layer cache makes a no-change rebuild take a couple of
seconds. That cost buys one guarantee that matters more: the sink that is
running can never be older than the code in this repo. It used to be
built only when the image was absent, which meant an install could keep
running a sink from before a change — and, in the multi-account upgrade,
`wa-up` would migrate your state to the new layout and then start a sink
that had never heard of it.

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

`wa-up` doesn't call `wa-heal` the instant wuzapi's API answers, though: a
previously-paired session's credentials survive the container recreate,
but wuzapi still has to reconnect to WhatsApp's servers, and answering
`/session/status` happens well before that reconnect finishes. Healing too
early would see `loggedIn: false` and correctly decide there's nothing to
do — skipping the one re-assert that matters. So `wa-up` waits (up to 30s)
for the session to actually log back in before handing off to `wa-heal`,
and tells you which of two different things happened:

- **Nothing was ever paired** — `wa-up` says so calmly and points you at
  `wa-pair <account-id> <number>`. Not a problem.
- **A paired session didn't log back in within 30s** — `wa-up` says the
  webhook could not be re-asserted, that incoming messages will be
  dropped until it is, and that the `wa-heal` timer will retry within five
  minutes, or you can run `bin/wa-heal` yourself right away.

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

This can be done from the terminal, exactly as below, or from the chat window's
settings screen (the gear, ⚙ — "+ Vincular cuenta nueva"), which drives
this same script under the hood. See "Settings screen" below for what the
UI path looks like; nothing about the underlying behaviour differs between
the two.

```sh
./bin/wa-pair <account-id> <number, international format, no +>
```

`<account-id>` names the account you're pairing — this is what
`accounts.json` will call this number for the rest of the system: it becomes
the wuzapi user, the default tone-file name, and the label shown on the
chat window's badges (see "Multiple accounts" below). On a fresh install, pick
whatever id you like for your first number, e.g.:

```sh
./bin/wa-pair personal 5491122334455
```

An account id may contain only lowercase ASCII letters, digits and
underscores. That restriction exists for a concrete reason, not style: the
id is upper-cased to build the name of the environment variable that holds
its token (`WA_TOKEN_<ID>`, e.g. `trabajo` → `WA_TOKEN_TRABAJO`). If two ids
differed only by case — `trabajo` and `Trabajo` — they would upper-case to
the *same* variable name, and whichever one paired second would silently
overwrite the first one's token in `secrets.env`; a reply meant for one
number could then go out from the other with nothing refusing it. This was
a real defect caught in review, not a hypothetical, and the charset rule is
what makes it impossible rather than merely unlikely. `wa-pair` refuses a
bad id (through `wa-reply token-var`, the one place this is checked) before
it creates a wuzapi user, writes a token, or touches `accounts.json` — a
rejected id never leaves a half-made account behind.

This prints an 8-digit linking code and tells you where to enter it:
**WhatsApp > Linked devices > Link device > Link with phone number**. Once
WhatsApp reports the session as logged in, `wa-pair` confirms the webhook
stuck and sets wuzapi's history to keep up to 100,000 messages for the
account (`wa-heal` re-asserts the same setting on its timer). It does
**not** purge anything: whatever WhatsApp sends of the history during
linking stays in `users.db`, unencrypted, and is what the chat window shows.

## Multiple accounts

You can link more than one WhatsApp number to the same install — for
example a personal number and a separate work number, each answered in its
own voice. Pair a second one exactly like the first, with a new account id:

```sh
./bin/wa-pair trabajo 5491122223333
```

That's the whole procedure — `wa-pair` creates a new wuzapi user, seeds an
entry for `trabajo` in `accounts.json`, and copies `agents/respuesta.md` to
`agents/trabajo.md` as a starting tone file, all before it walks you through
linking the phone the same way described above.

**Where per-account settings live.** Every configured account is one entry
in `~/.local/share/omarchy-wa/state/accounts.json`, a JSON list. Each entry
has:

- `id` — the account id you gave `wa-pair`.
- `label` — shown on the chat window's badges and in the "sale desde" line;
  defaults to the id with underscores turned into spaces and title-cased
  (`trabajo` → `Trabajo`), and you can edit it to anything you like.
- `wuzapi_user` — the name of the wuzapi user this account maps to; this is
  what arrives as the webhook's `instanceName` and is how the sink knows
  which account a message belongs to. Normally the same as `id`.
- `agent` — the filename, under `agents/`, of this account's tone file —
  the instructions `claude -p` is given when drafting a reply for this
  account. Edit `agents/<that file>` to change how this account replies.
- `quick_replies` — this account's own one-click reply texts, a plain list
  of strings shown as buttons in the chat window when a chat from this account is
  open. Edit this array by hand to add, change or remove one; no restart
  needed, the chat window re-reads it (via `wa-reply quick`) every time you open a
  chat.

To rename an account's label, edit its entry in `accounts.json` directly and
save — there's no command for this today. Its tone and quick replies can
also be hand-edited the same way (the file named in its `agent` field, and
its `quick_replies` array respectively), or edited through the chat window's
settings screen (the gear, ⚙ — see "Settings screen" below), which reads and
writes the exact same files via `wa-reply tone-get`/`tone-set` and
`quick-get`/`quick-set`. Neither is a separate storage layer; the screen is
another door onto the same files.

**`groups_enabled` is not per account.** It stays exactly where it always
was, in `~/.local/share/omarchy-wa/state/config.json`, shared by every
account (see "Enabling a group" below). That's deliberate, not an
oversight: a group JID is unique across all of WhatsApp, and a given group
either has one of your accounts as a member or it doesn't — there is no
scenario where the same JID needs different enablement per account, so
splitting this setting per account would only add a place for the same
JID to disagree with itself.

**In the chat window:** every chat in the list is tagged with its account's
label, and the conversation states plainly which number a message will go
out from ("Sale por: <cuenta>") — never a choice, since a reply always
leaves by the number it arrived on (see "What it does" above).

### Migrating from a single-account install

If you're upgrading an install that predates multi-account, you don't need
to do anything by hand — just run `./bin/wa-up`. In that order, it:

1. rebuilds the `omarchy-wa-sink` image from this repo (always, see
   "What gets built" above), so the sink about to run is the new one;
2. **stops both containers** (`docker rm -f`), so nothing is writing to
   your state while it changes shape. This is why the migration is safe:
   a message arriving mid-migration would otherwise be written by the
   still-running old sink *after* the queue had been rewritten, and would
   stay in the old shape forever, since the migration only ever runs once;
3. migrates your existing setup to the `accounts.json` layout;
4. starts both containers again, the sink now on the new image.

Your bridge is down for the length of steps 2–4 (seconds), and messages
that arrive in that window are delivered by WhatsApp when it comes back
up, the same as after any restart. You'll see one of these two lines:

```
migrated to accounts.json
```
or, on every run after the first:
```
accounts.json already present
```

The migration creates a single account called `personal` (`wuzapi_user:
omarchy`, matching the wuzapi user your install already had), copies your
existing `WA_TOKEN` to `WA_TOKEN_PERSONAL` in `secrets.env` (the original
`WA_TOKEN` is left in place, untouched), moves whatever `quick_replies` you
already had in `config.json` onto that account, and moves your existing
per-chat context files into `state/ctx/personal/`. **Your pending
conversations are preserved**: every queue entry already sitting in
`queue.json` is rewritten to carry `"account": "personal"` rather than
dropped or reset, and none of your quick replies or context history is
lost in the process. `groups_enabled` in `config.json` is left exactly
where it was.

This is safe to run more than once and safe to interrupt: it is a no-op
once `accounts.json` exists, and if it's ever interrupted partway (a crash
mid-`wa-up`), the next `wa-up` picks up wherever it left off rather than
redoing finished steps or losing anything.

## Settings screen

The gear (⚙) in the chat window's top-right corner opens a settings screen
embedded in the chat window; the same button, now labelled "Volver", closes
it and returns to the conversation exactly where you left it. It lists
every configured account, and from there you can link a new one, edit an
existing account's tone or quick replies, or unlink it. It never sends a
message — there is no send path in the settings screen at all, only
`wa-pair`/`wa-unlink`/`wa-reply`'s read and write subcommands for tone and
quick replies — and it keeps running a link or an unlink in the background
even if you close the settings screen or the whole chat window while one is
mid-flight.

Above the account list, a "Caché de adjuntos" box shows how much disk the
attachment cache takes (from `bin/wa-media size`) and a **Vaciar caché**
button that empties it, for every account, in one click and without a
confirmation step — see "Attachments" below.

### Linking from the UI

"+ Vincular cuenta nueva" asks for the same two things the terminal command
does — an account id and a phone number — then runs `wa-pair` in `--json`
mode and shows its progress one stage at a time:

1. **Validating** the account id.
2. **The code** — the same 8-digit linking code `wa-pair` prints on the
   terminal, shown large, with the same instructions: WhatsApp > Linked
   devices > Link device > Link with phone number.
3. **Linked. Confirming the webhook…**, then the screen reports done.

If `wa-pair` is killed mid-run (a bar restart, a crash) before it reports
its final result, the screen says so plainly instead of guessing — it does
not know whether the account ended up linked, and tells you to check by
hand (the account list, or `docker logs omarchy-wa-sink`) before retrying.

**The terminal path still works, unchanged, and is not deprecated:**

```sh
./bin/wa-pair <account-id> <number, international format, no +>
```

Its `--json` mode (`./bin/wa-pair <account-id> <number> --json`, one JSON
object per line) is exactly what the settings screen runs and consumes —
there is no separate, UI-only pairing path.

A settings screen that makes linking easier does not make linking safer.
If anything it lowers the friction on an action that carries the ban risk
described at the top of this README — read that warning before you use
either path, terminal or UI.

### Unlinking

"Desvincular" next to an account asks for a typed confirmation: you must
type the account id exactly before the button enables. Before asking, it
fetches — fresh at that moment, never cached from when the screen was
opened — how many pending conversations for that account it is about to
discard, and states the count in the confirmation text. It refuses to
proceed if that count could not be fetched, so you are never asked to
confirm against an unknown number.

Those pending conversations are discarded, not preserved, because
`bin/wa-reply` refuses forever to answer a queue entry whose account no
longer exists (see "What it does, in short" above) — leaving them in the
queue after the account is gone would strand them there, visible and
permanently unanswerable. Discarding them at unlink time is the honest
outcome, not a limitation to route around.

**Unlinking does not remove the tone file** under `agents/<account-id>.md`.
That is deliberate: it holds no credential, it is prose you wrote and
edited by hand, and re-linking the same account id later gets it back
instead of making you write it again.

**Unlinking does not empty the account's attachment cache** either
(`media/<account-id>/`, see "Attachments" below). Empty it with
**Vaciar caché** (every account) or `./bin/wa-media clear --account
<account-id>` (just that one).

**Unlinking does remove the account's wuzapi user**, not just its session.
That is what makes unlink-then-relink of the same account id work: a
logged-out user left behind in wuzapi would still own the old token, so
the next `wa-pair` for that id would create a fresh token, be told the
user "already exists", and then fail every call with a token wuzapi does
not recognise. If the user cannot be removed — wuzapi unreachable, or no
`WUZAPI_ADMIN_TOKEN` on file — the local cleanup still finishes (a
container that is down must never leave an account un-removable here), but
the screen says so explicitly instead of reporting a clean unlink, because
that leftover user is exactly what would break your next attempt at
linking that id.

**If a step fails, the unlink stops there and says which one.** Every local
step — `accounts.json`, the token, the context ring, the queue entries —
is checked, and a failure (an unwritable state directory, a full disk)
ends the run with an error naming that step rather than announcing
"desvinculada" over an account that is still on disk. Removing the account
from `accounts.json` is the commit point: a failure before it changes
nothing, and a failure after it leaves an account that `wa-reply` already
refuses as unknown, so re-running `./bin/wa-unlink <account-id>` once the
cause is fixed finishes the job.

The equivalent terminal command:

```sh
./bin/wa-unlink <account-id>
```

with `--json` (the same event vocabulary as `wa-pair --json`, and the
settings screen's own transport) and `--count-only` (prints just the
pending-discard count for that account and changes nothing — what the
confirmation dialog above fetches first). See "Unlinking" further below for
what unlinking does and does not do to the WhatsApp-side device list, and
for tearing down the whole install instead of one account.

### Editing tone and quick replies

"Tono" and "Respuestas rápidas" open an editor for that account's tone file
(`agents/<account-id>.md`) and its `quick_replies` list in `accounts.json`,
respectively. Both remain ordinary hand-editable files — see "Multiple
accounts" above — the settings screen is another door onto the same files,
not a replacement for editing them directly.

**Save conflicts.** Both editors read the file's content and a hash of it
when opened, and send that hash back with the save. If the file changed on
disk since it was read, the save is refused outright and nothing is
written — never merged, never silently overwritten. Use "Recargar" to pick
up the current content, then reapply your edit. "Recargar" throws away
whatever is in the box, so with unsaved edits it asks first: the button
turns into "¿Descartar cambios?" and only the second press reloads.

The tone editor starts empty and its "Guardar" button stays disabled until
that account's own text has actually been read. Opening Tono for one
account, going back, and opening it for another shows nothing in between
rather than the first account's prose under the second's heading.

The quick-replies guard is coarser than the tone guard: it hashes the
*whole* `accounts.json` file, not just the one account's `quick_replies`
entry, so a hand edit (or another settings-screen save) to a *different*
account between opening this screen and saving can also block this save,
even though nothing about the account you're editing actually conflicted.
This never loses data — it only makes you reload and retry — but it is
coarser than it strictly needs to be, and the screen's own text says so.

## Enabling a group

Group chats are ignored by default. `groups_enabled` is shared by every
configured account, not set per account — see "Multiple accounts" above for
why. To let one through, add its JID to `groups_enabled` in
`~/.local/share/omarchy-wa/state/config.json`:

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

The conversation shows a row of quick-reply buttons above the composer.
**Clicking one sends that exact text immediately — there is no
confirmation step, no undo, and no draft to review first.** It is not a
shortcut for filling the composer; the message goes out on that click
(still subject to dry-run). The buttons are disabled while a send or a
draft is in progress. If the chat's last send has an unknown outcome (see
"Sends whose outcome nobody knows"), the guard lives in `wa-reply
chat-send`, not in the button: the click is refused and the window shows
its confirmation banner instead.

Quick replies are per account: each account's own list lives in its
`quick_replies` field in `~/.local/share/omarchy-wa/state/accounts.json`
(see "Multiple accounts" above), as a plain list of strings, for example:

```json
[{"id": "personal", "label": "Personal", "wuzapi_user": "omarchy",
  "agent": "respuesta.md",
  "quick_replies": [
    "Hola, soy el asistente AI de Cristóbal, en estos momentos no puede responder pero responderá en breve.",
    "Gracias, te respondo apenas pueda."
  ]}]
```

`wa-pair` seeds a new account's `quick_replies` with one default entry when
it creates that account; it never touches an existing account's list. A
migrated single-account install has its old `quick_replies` (formerly in
`config.json`) carried onto the `personal` account instead — see
"Migrating from a single-account install" above.

To add, edit or remove a quick reply for a given account: edit that
account's `quick_replies` array in `accounts.json` by hand (add a string,
change one, or delete one) and save. There's no restart needed — the chat window
re-reads it from `wa-reply quick` every time you open a chat that belongs
to that account.

## The per-chat context ring

Drafting reads a small per-chat context ring, written by the sink: at most
the last 10 messages per chat, per account (`state/ctx/<account-id>/`, see
"Where state lives"), in both directions. Which side a message is on comes
from an explicit `direction` mark, never from comparing names. Your side of
it is whatever WhatsApp reports as sent **by you**, from any device,
including this plugin (whose sends come back as that echo). The drafting
prompt sees both sides, labelled "Yo" for yours. The chat window itself
reads the full history from `users.db`, not this ring. Unlinking an account
removes its ring.

## Attachments

When someone sends a photo, a voice note, a video, a sticker or a
document, the message in the conversation carries a label for it
(`[imagen]`, `[audio]`, ...). Click the label to download it, and the
file is then handled by type:

- **Images and stickers** — shown inline under the message, decoded at
  roughly the size they are shown; click the image to open it in your
  system viewer.
- **Voice notes and audio** — play in the chat window itself; the label
  becomes ▶ / ⏸ with elapsed/total time, and clicking toggles it.
- **Video and documents** — handed to your system viewer with `xdg-open`.
- **Locations and contacts**, or an attachment whose event arrived missing a
  field the download needs, are labelled "(no disponible)": there is nothing
  to download (wuzapi has no endpoint for them).

The label says "descargando…" while it works. A download that fails shows
why, ending in "(clic para reintentar)". A file that was in the cache but
has since been pruned is reported the same way, never as a broken image.
Any message's attachment can be opened, not only the latest one in a chat
(`wa-media get-msg`). Viewing is the whole feature: there is no way to
*send* an attachment from the chat window.

**Who downloads.** `bin/wa-media` does, never the sink: the sink still
holds no credentials. Like `wa-reply`, it takes the account from the queue
entry and nothing else — no flag, no environment variable — uses that
account's token, refuses an entry whose account is not in
`accounts.json`, and only ever talks to wuzapi on `127.0.0.1`. Dry-run
mode does not apply here: it gates sending, and downloading sends nothing.

```sh
./bin/wa-media get <entry-id>             # download (or reuse) and print its path
./bin/wa-media size [--account <id>]      # bytes the cache takes on disk
./bin/wa-media clear [--account <id>]     # empty the whole cache, or one account's
```

### The cache, and what it means for privacy

Downloaded attachments are kept in:

```
~/.local/share/omarchy-wa/media/<account-id>/
```

(under `$WA_STATE_ROOT` if you moved it). Directories are mode 0700,
files 0600. The file names are hashes, not message ids or phone numbers.
`media/` sits next to `state/`, not inside it, so it is not mounted into
any container.

**These files are stored unencrypted on disk.** That includes photos,
voice notes and documents sent by third parties who have no idea this
plugin exists. The text history is stored unencrypted too (see the top of this
README), and that was the owner's deliberate choice, made after seeing this
exact cost — not an oversight.
If you don't want it, empty the cache and don't press the buttons.

The cache is bounded:

- **30 days** maximum age and **500 MiB** total, by default. Both can be
  changed in `state/config.json` with `media_cache_max_age_days` and
  `media_cache_max_bytes` (a missing, zero, negative or non-numeric value
  falls back to the default).
- Pruning runs **after each new download** — not on a timer — deleting
  files past the age limit first, then the oldest files until the total
  fits. The file that was just downloaded is never pruned, so the cache can
  exceed the limit by that one file. Between downloads, an old file stays
  on disk until the next download or until you empty the cache.
- A second request for the same attachment reuses the cached file instead
  of downloading it again.

To empty it: the **Vaciar caché** button in the settings screen (whole
cache, all accounts, no confirmation), or `./bin/wa-media clear` — with
`--account <id>` to empty just one account's. `./bin/wa-media size` tells
you how much it currently takes. Unlinking an account does **not** empty
that account's media; run `./bin/wa-media clear --account <id>` for that.

## Container logs and message content

`bin/wa-up` starts wuzapi with `LOG_LEVEL=warn`. Left unset, wuzapi defaults
to its most verbose level and writes the full text of every message —
yours and the other party's — into `docker logs omarchy-wuzapi` in
plaintext. Message content in a Docker journal is a second, less visible copy of
the history, with no paging or cleanup. `warn` keeps connection problems
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
report what it *would* have sent. No request ever reaches wuzapi: the chat
window shows "DRY RUN: no se envió nada." and keeps what you typed, so you
can keep iterating on the draft. A dry run never clears an
unknown-outcome mark (see the next section). This is the
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

A send can end in a state where the message may already have reached
WhatsApp and nothing recorded the answer: the POST timed out, the
connection dropped mid-request, or `wa-reply` was killed (a bar restart, an
OOM kill) between starting the POST and learning its result. The request
can take up to 20 seconds, so this window is real.

`wa-reply chat-send` guards against it with a per-chat mark in
`state/chat_unknown.json`. The mark is written **before** the request goes
out and cleared only when the outcome is definite: wuzapi confirmed the
send, answered with an error, or refused the connection (nothing was
listening, so nothing left). A timeout or a dropped connection leaves it,
and so does closing the window mid-send (Esc, ✕ or the bar button), which
kills `wa-reply` before it can write anything. A chat that also has a
queue entry keeps the same guard on that entry's status instead. The chat
window shows:

> Sin confirmación: puede que ya haya salido. Revisá WhatsApp antes de
> reintentar.

The mark is durable, and the protection is enforced by `chat-send` itself:

- `wa-reply chat-send` **refuses** a chat with a mark and returns
  `{"ok": false, "needs_ack": true, ...}` unless it is called with
  `--acknowledged`. That includes quick replies and Enter.
- The window turns that refusal into a banner with two buttons: **"Revisé
  WhatsApp, enviar igual"**, which resends with `--acknowledged`, and
  **Cancelar**, which drops the pending text.
- A new incoming message in the same chat does **not** clear the mark, and
  neither does closing the window, restarting the bar or a dry run: it is
  recomputed from what is on disk. Check WhatsApp yourself to see whether
  the message went out before you press the button.

## What takes a chat out of the queue

This section is about the **bar counter** and `queue.json`, not about the
window's chat list. The window shows every chat, and marks one read on the
phone only when you click it (`state/read.json`); nothing below removes a
chat from the window.

A chat leaves the counter's queue in three ways:

- **You answer it from the chat window** (or any other device) and the
  send is confirmed.
- **You answer it from another device** — the phone, WhatsApp Web,
  ZapZap. WhatsApp reports your own message to every linked device; the
  sink records it in the context ring as yours and removes that chat's entry.
- **You read it on another device.** When one of your other devices marks
  the chat as read, WhatsApp sends a read receipt and the sink removes that
  chat's entry — **even if you didn't answer it**. This is deliberate: the
  owner chose a short queue knowing that a message you read and meant to
  answer later disappears from it too. If you rely on the counter as a
  to-do list, don't open those chats on the phone, or keep them unread there.

Read receipts travel in both directions, and only one of them counts. The
sink acts only on wuzapi's `ReadSelf` state — *you* read *their* message on
another device. The plain `Read` state — *they* read *your* message — never
clears anything; otherwise any contact opening your message would empty
the very chat that is waiting for their answer.

A read receipt or own message for a chat with no entry does nothing, and
one arriving on a number not in `accounts.json` is dropped and logged once.
New incoming messages queue exactly as before.

Read receipts only reach the sink if the account's webhook subscribes to
them: `wa-pair` subscribes new accounts to `Message` and `ReadReceipt`,
and `wa-heal` re-asserts that same subscription on every run, so an
already-paired install picks it up on the next timer tick without
re-pairing.

The plugin's own sends come back to the sink as a message from you too,
and that echo clears the chat's entry like any other.

## Where state lives, and how to wipe it

Everything lives under `~/.local/share/omarchy-wa/` (mode 700):

```
~/.local/share/omarchy-wa/
├── secrets.env     mode 600 — mounted into NO container
├── state/          mounted into the sink at /state
│   ├── queue.json
│   ├── accounts.json
│   ├── ctx/
│   │   └── <account-id>/
│   └── config.json
├── media/          attachment cache — mounted into NO container
│   └── <account-id>/
└── wuzdata/        mounted into both containers
```

- `secrets.env` — the admin token, encryption key, and one API token per
  paired account (`WA_TOKEN_<ID>`, e.g. `WA_TOKEN_PERSONAL`,
  `WA_TOKEN_TRABAJO`), plus (if you set it) `WA_DRY_RUN=0`. Mode 600. It
  sits **outside** `state/` on purpose: the sink is the container that
  receives whatever arrives from the internet, and it has no business
  holding a credential that can send messages as you. It is not
  bind-mounted anywhere.
- `state/queue.json` — pending messages shown in the chat window, each carrying
  which account it arrived on.
- `state/accounts.json` — the configured accounts: one entry per number,
  see "Multiple accounts" above.
- `state/ctx/<account-id>/` — one small JSON file per chat, per account:
  the last 10 messages in both directions, used when drafting (see "The per-chat context ring" above).
- `state/config.json` — `groups_enabled` (shared by every account),
  the optional media cache limits `media_cache_max_bytes` and
  `media_cache_max_age_days`, and any other hand-edited, install-wide
  settings. Quick replies are no longer here — they live per account in
  `accounts.json`.
- `media/<account-id>/` — downloaded attachments, **unencrypted**, up to
  500 MiB / 30 days by default. Written only by `bin/wa-media` on the
  host; emptied with `./bin/wa-media clear` or the settings screen's
  **Vaciar caché** (see "Attachments" above).
- `wuzdata/` — wuzapi's own SQLite database and session data, bind-mounted
  into both containers. Its `users.db` holds the full message history
  (up to 100,000 per account), **unencrypted** and no longer purged: the chat
  window reads it. It also holds each session's login.
- `state/read.json` — read markers (row ids) per chat; `state/changed` is
  touched by the sink on each event so the window and `wa-notify` refresh;
  `state/focused` is written by the window so `wa-notify` knows which chat
  you are looking at; `state/chat_unknown.json` records chat sends whose
  outcome is unknown (see "Sends whose outcome nobody knows").

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
and the chat window all reported it as empty, because a permission error and an
empty queue looked identical. `wa-up` now detects state files it can't
read as your user and repairs their ownership automatically before
(re)starting anything; if it can't (e.g. Docker itself isn't reachable),
it prints the exact command to run by hand and refuses to start rather
than leave a broken install running. If you ever see this again, unreadable
or corrupt state now says so loudly instead of quietly reporting zero:
`wa-reply list` prints `{"error": "..."}` (never a bare `[]`) and exits
non-zero, `wa-status` reports `ok: false` (which the bar widget shows as
`WA ✕`), and the chat window shows a red banner instead of an empty list.

To wipe everything and start clean:

```sh
docker rm -f omarchy-wuzapi omarchy-wa-sink
rm -rf ~/.local/share/omarchy-wa
```

## Unlinking

To remove **one** account and leave the rest of the install running, use:

```sh
./bin/wa-unlink <account-id>
```

or the settings screen's "Desvincular" button (see "Settings screen"
above for the confirmation flow, what it discards, and why). This logs the
account out of wuzapi, removes that account's wuzapi user (so re-linking
the same id later works instead of colliding with the old one), removes it
from `accounts.json`, removes its token from `secrets.env`, removes its
context ring, and discards its pending queue entries — but deliberately
leaves `agents/<account-id>.md` (its tone file) in place. It also leaves
that account's attachment cache (`media/<account-id>/`) on disk; empty it
with `./bin/wa-media clear --account <account-id>`. The two wuzapi
steps are best-effort, because an unreachable container must not make a
local account un-removable; either one failing is reported, never
swallowed. It does **not** by itself remove the device from
WhatsApp's own "Linked devices" list; do that on the phone too if you want
WhatsApp to consider the number unlinked, not just this install.

To tear down the **whole** install — every paired account at once, not
just one — `docker rm -f`/`rm -rf` below is still the way:

1. On **each phone number** you paired: **WhatsApp > Linked devices**, and
   remove this device from the list. Do this first, for every account —
   it's the step that actually tells WhatsApp the link is over, and
   step 2/3 delete the local record of which numbers you'd paired, so
   there's nothing left here afterwards to remind you which phones still
   need it done.
2. `docker rm -f omarchy-wuzapi omarchy-wa-sink`
3. `rm -rf ~/.local/share/omarchy-wa`

Skipping step 1 leaves the device listed as linked on WhatsApp's side even
after you've torn down the containers here.

## License

MIT
