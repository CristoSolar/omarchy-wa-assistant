// Panel.qml
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// The pending-replies popup: a list of queued chats, and a detail view to
// draft and send a reply to whichever one is selected.
//
// Built on the shell's own Panel/KeyboardPanel base rather than a hand-rolled
// PopupWindow: PopupWindow (an xdg-popup) never receives keyboard focus
// except through a click/hover on its parent surface, which is why the idea
// field used to be untypeable. KeyboardPanel is a layer-shell surface that
// briefly primes Wayland-exclusive keyboard focus on open and then hands it
// to `focusTarget`, so a TextField inside it can `forceActiveFocus()` for
// real. BarWidget.qml owns the bar pill and hands this panel the item to
// anchor against.
Panel {
  id: root
  moduleName: "io.github.cristosolar.waassistant"
  ipcTarget: "io.github.cristosolar.waassistant"
  manageIpc: false

  property Item anchorItem: null
  property var hostWidget: null

  property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
  property var entries: []
  // The selected entry, and its id kept separately so refresh() can
  // re-resolve the entry object from the freshly loaded queue. Anything
  // the UI decides about an entry must come from what is on disk, not
  // from a snapshot taken when it was clicked.
  property var current: null
  property string currentId: ""
  // Set when `wa-reply list` reports it could not read the queue (a
  // broken install — permissions, corruption — never "nothing queued").
  // Kept apart from a genuinely empty `entries: []`, which must render
  // as a quiet empty panel, not as this banner.
  property string storeError: ""
  property string draft: ""
  // The idea that produced the current draft, kept only for the "Idea: …"
  // recap line above the composer once a draft exists. Cleared whenever
  // the composer goes back to idea-entry mode.
  property string idea: ""
  // True for the ~7s round trip while `claude -p` is drafting. Distinct
  // from inFlight (which is send-only): drafting never touches the send
  // Process, but it still has to visibly disable the composer so the
  // user isn't left wondering whether Enter/Redactar did anything.
  property bool drafting: false
  property string status: ""
  // The status line turns to the theme's attention color for any outcome
  // that is neither a clean success nor a clean failure.
  property bool statusWarn: false
  // Derived from the DURABLE status in queue.json, never from a flag we
  // set ourselves: an entry left "unknown" by an earlier send may already
  // have reached the recipient. A bar restart, a Loader reload or a
  // refresh cannot clear this, because it is recomputed from disk every
  // time. `wa-reply send` refuses such an entry too, so the two halves of
  // the defence agree.
  readonly property bool needsAck: root.current !== null
                                   && (root.current.status === "unknown"
                                       || root.current.status === "sending")
  // The user's deliberate acknowledgement for the current selection. It
  // is intentionally volatile — reselecting, reopening or restarting
  // drops it, and `needsAck` comes straight back from the stored status.
  property bool acknowledged: false
  // Only for the one case the stored status cannot describe: a send whose
  // response could not be parsed at all, where we do not know what
  // wa-reply managed to record.
  property bool sendBlocked: false
  // Guards a single "send" round trip. The send has its own Process, so
  // another command can no longer reassign the handler out from under it,
  // but a rapid double-click on Enviar still has to be refused.
  property bool inFlight: false

  // Which of the two screens a selected chat shows: false is the extract
  // (read the recent thread, pick "Responder"), true is the composer
  // (write and send). Both live under `root.current !== null`, so the
  // gear stays hidden and there is still exactly one way back to the
  // list — through the extract's own Volver, never a second path. Reset
  // to false by select(), so reopening a chat always starts at reading,
  // never lands back inside the composer from a previous visit.
  property bool composing: false

  // Toggled by the gear. SettingsView.qml is a plain child (not a Loader),
  // never destroyed, so a link or unlink it has mid-flight keeps running
  // and keeps its state while this is false — see its own onShown().
  // SettingsView has no path to sendProc, doSend() or root.run(): it
  // cannot send a message by construction, not just by discipline.
  property bool settingsOpen: false

  // One-click quick replies (config.json's quick_replies, via
  // `wa-reply quick`). Loaded through its own Process so fetching it can
  // never race the list/draft Process or the send Process.
  property var quickReplies: []

  // The extract: the current entry's stored context ring, oldest first,
  // via `wa-reply thread` (read-only — Task 2). Its own Process, for the
  // same reason quick replies get one: fetching it must never race
  // list/draft/send. `threadError` is kept apart from a genuinely empty
  // `threadEntries: []` the same way `storeError` is kept apart from an
  // empty queue — an empty ring for an old conversation is the NORMAL
  // case (see the explanatory text on the extract screen) and must never
  // look like this.
  property var threadEntries: []
  property string threadError: ""

  // The current entry's attachment descriptor (Task 1/2 of the media plan),
  // or null for a plain text entry. Read straight off root.current — the
  // extract's own thread ring never carries this (bin/wa-reply's `thread`
  // strips every field but direction/text), and it must not, because the
  // ring holds older messages this task never asked for a viewer on. Only
  // the entry actually pending a reply gets one.
  readonly property var currentAttachment: root.current ? (root.current.attachment || null) : null
  readonly property bool hasAttachment: root.currentAttachment !== null && root.currentAttachment !== undefined
  readonly property bool attachmentDownloadable: root.hasAttachment && root.currentAttachment.downloadable === true
  // Images and stickers are the only types this panel ever displays
  // inline (both cache to a raster file wa-media can hand back — see its
  // EXTENSIONS map). Everything else downloadable (audio, video,
  // document) opens with the system viewer instead — inline playback and
  // video preview are explicitly out of scope for this task.
  readonly property bool attachmentIsImage: root.hasAttachment
      && (root.currentAttachment.type === "image" || root.currentAttachment.type === "sticker")

  // Media download state (Task 3 of the 2026-09-23 media-viewing plan).
  // Kept apart from `status`/`statusWarn` (the send/draft status line)
  // because a media fetch and a send/draft can be relevant at once — the
  // extract screen where the attachment lives never overlaps the composer
  // where `status` is shown, but keeping them separate properties means
  // neither path has to guess whether the other cleared it.
  property string mediaPath: ""
  property string mediaError: ""
  property bool mediaLoading: false
  // True from the moment xdg-open is launched (audio/video/document)
  // until it actually exits, either way — the visible "still working"
  // state for a step openProc's own onExited settles honestly rather
  // than assuming success the instant it starts.
  property bool openPending: false
  // Guards a stale response the same way requestDraft()'s `requestId`
  // does: if the user reselects a different chat while a download is in
  // flight, the late answer must not paint over the newly selected entry.
  property string mediaRequestId: ""

  // account id -> label, via `wa-reply accounts`. The queue entry carries
  // only the account ID, never a label: a label lives exclusively in
  // accounts.json, so a rename there can never leave a stale copy
  // disagreeing with it somewhere in the queue. This is asked for fresh
  // on every refresh() rather than cached from the entry, through its
  // own Process for the same reason quick replies get one — it must
  // never race list/draft/send.
  property var accountLabels: ({})

  // A plain-language label for an attachment type, for the "no se puede
  // ver esto" message — never the raw type string on its own, which reads
  // as a code, not an explanation.
  function attachmentTypeLabel(atype) {
    var labels = {
      "image": "una imagen", "sticker": "un sticker", "audio": "un audio",
      "video": "un video", "document": "un documento",
      "ubicación": "una ubicación", "contacto": "un contacto",
    }
    return labels[atype] || ("un adjunto de tipo " + atype)
  }

  // The label for one account id, or the id itself when accounts.json
  // has nothing for it (a stale entry from a removed account, or the
  // labels haven't loaded yet) — never a blank badge.
  function accountLabel(accountId) {
    if (!accountId)
      return ""
    return root.accountLabels[accountId] || accountId
  }

  // Guarded so the panel renders before the bar injects itself.
  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property color contentUrgent: bar ? bar.urgent : Color.urgent
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    root.controller.show()
  }
  function close() {
    root.controller.hide()
  }
  function toggle() {
    if (root.opened) root.close(); else root.open()
  }

  Component.onCompleted: root.refresh()
  onOpenedChanged: if (root.opened) root.refresh()

  // Everything except the send. Refuses to start while a send is in
  // flight: these used to share one Process with the send, so pressing
  // Enter in the idea field or toggling the popup mid-send reassigned
  // proc.command/proc.done, the send's completion handler never ran, and
  // inFlight stayed true — the send button was dead for the rest of the
  // session and the user never learned the outcome of a send that had
  // actually happened.
  function run(args, done) {
    if (root.inFlight || proc.running)
      return false
    proc.command = [root.pluginDir + "bin/wa-reply"].concat(args)
    proc.done = done
    proc.running = true
    return true
  }

  Process {
    id: proc
    property var done: null
    stdout: StdioCollector {
      onStreamFinished: if (proc.done) proc.done(text)
    }
  }

  // The send path owns its own Process so nothing else can touch it.
  Process {
    id: sendProc
    property var done: null
    stdout: StdioCollector {
      onStreamFinished: if (sendProc.done) sendProc.done(text)
    }
  }

  // Its own Process too, for the same reason: fetching the quick-reply
  // list must never be blocked by, or block, list/draft or send.
  Process {
    id: quickProc
    stdout: StdioCollector {
      onStreamFinished: {
        var parsed = null
        try { parsed = JSON.parse(text) } catch (e) { parsed = null }
        root.quickReplies = Array.isArray(parsed) ? parsed : []
      }
    }
  }

  // Quick replies are per-account (Task 5), and `quick` now takes the
  // shown entry's id rather than nothing, so it can resolve which
  // account's list to return. Called with "" (nothing selected) just
  // clears the list instead of shelling out — the list view never shows
  // quick-reply buttons, so there is nothing to fetch for it.
  function loadQuickReplies(entryId) {
    if (quickProc.running)
      return
    if (!entryId) {
      root.quickReplies = []
      return
    }
    quickProc.command = [root.pluginDir + "bin/wa-reply", "quick", entryId]
    quickProc.running = true
  }

  // Its own Process too, for the same reason quick replies and account
  // labels get one: fetching the extract must never race list/draft/send.
  Process {
    id: threadProc
    stdout: StdioCollector {
      onStreamFinished: {
        var parsed = null
        try { parsed = JSON.parse(text) } catch (e) { parsed = null }
        if (Array.isArray(parsed)) {
          root.threadEntries = parsed
          root.threadError = ""
        } else {
          // wa-reply always prints an array on this subcommand, even on
          // a vanished entry — this branch is an unexpected shape, not
          // the normal empty-ring case, so it gets the error banner
          // rather than silently rendering as "nothing happened yet".
          root.threadEntries = []
          root.threadError = "wa-reply thread dio una respuesta inesperada"
        }
      }
    }
  }

  function loadThread(entryId) {
    if (threadProc.running)
      return
    if (!entryId) {
      root.threadEntries = []
      root.threadError = ""
      return
    }
    threadProc.command = [root.pluginDir + "bin/wa-reply", "thread", entryId]
    threadProc.running = true
  }

  // Its own Process too, for the same reason everything above gets one:
  // downloading an attachment must never race list/draft/send/thread.
  // `bin/wa-media` is the ONLY thing that ever downloads — this Process
  // shells out to it exactly the way every other Process here shells out
  // to wa-reply, and never touches wuzapi or a token directly.
  //
  // `terminalSeen`/`requestId` mirror SettingsView.qml's pairProc/
  // unlinkProc pattern: `done` (fired from onStreamFinished) is the
  // terminal event, and if the process exits without one ever having
  // fired — killed, crashed before writing a line — `onExited` below is
  // what stops the spinner and reports it, rather than leaving
  // `mediaLoading` true forever with no way to retry.
  Process {
    id: mediaProc
    property var done: null
    property bool terminalSeen: false
    property string requestId: ""
    stdout: StdioCollector {
      onStreamFinished: {
        mediaProc.terminalSeen = true
        if (mediaProc.done) mediaProc.done(text)
      }
    }
    onExited: function (exitCode) {
      if (mediaProc.terminalSeen)
        return
      // Stale: the user already moved on (select() already reset every
      // media property), so there is nothing left here to correct.
      if (root.mediaRequestId !== mediaProc.requestId)
        return
      root.mediaLoading = false
      root.mediaPath = ""
      root.mediaError = "wa-media get terminó (código " + exitCode
          + ") sin responder; no se sabe si el adjunto se descargó. Probá de nuevo."
    }
  }

  // Hands the cached path to the system's own viewer for audio/video/
  // document. Inline playback is out of scope by design (see the
  // media-viewing spec), so this IS the whole action for those types —
  // but "started" is not "succeeded": xdg-open can fail (missing binary,
  // no handler registered, a broken $PATH), and reporting success
  // regardless of its exit code is exactly the confident-lie shape this
  // project has repeatedly shipped and corrected elsewhere today. Its
  // exit code is checked below, honestly, rather than assumed.
  Process {
    id: openProc
    property string requestId: ""
    onExited: function (exitCode) {
      root.openPending = false
      if (root.mediaRequestId !== openProc.requestId)
        return  // stale: the user already moved on to a different entry
      if (exitCode !== 0) {
        // The download itself succeeded (mediaPath was already set) —
        // only the handoff to the system viewer failed. Clearing
        // mediaPath re-shows the button as "Reintentar" rather than
        // leaving a silent "Se abrió…" claim on screen that never
        // actually happened.
        root.mediaPath = ""
        root.mediaError = "no se pudo abrir el adjunto con el visor del "
            + "sistema (xdg-open salió con código " + exitCode + ")."
      }
    }
  }

  // Downloads (or re-serves from cache) the current entry's attachment via
  // `wa-media get`, idempotent on wa-media's own side. Refuses to start a
  // second download while one is already running, exactly like every
  // other run()-style guard in this file.
  //
  // A failure is shown, not swallowed: this is the one path the spec calls
  // out by name ("a download that fails says so and lets the user try
  // again... must not spin forever, and must not look like an empty
  // result") — the mistake it must not repeat is exactly the one this
  // project has shipped before, an empty/blank state that actually meant
  // "could not read it".
  function fetchMedia() {
    if (mediaProc.running || !root.currentId || !root.attachmentDownloadable)
      return
    var requestId = root.currentId
    root.mediaRequestId = requestId
    root.mediaError = ""
    root.mediaLoading = true
    mediaProc.terminalSeen = false
    mediaProc.requestId = requestId
    mediaProc.command = [root.pluginDir + "bin/wa-media", "get", requestId]
    mediaProc.done = function (out) {
      // The selection may have moved on while this was in flight — same
      // stale-response guard requestDraft() uses. Nothing here may touch
      // root.mediaPath/mediaError/mediaLoading for a chat the user is no
      // longer looking at.
      if (root.mediaRequestId !== requestId)
        return
      root.mediaLoading = false
      var parsed = null
      try { parsed = JSON.parse(out) } catch (e) { parsed = null }
      if (parsed && parsed.ok && parsed.path) {
        root.mediaPath = parsed.path
        root.mediaError = ""
        // Only the two inline-image types stay in the panel; everything
        // else downloadable hands straight off to the system viewer —
        // there is no "open" button distinct from the download button for
        // those, one click does both steps. Its own exit code is what
        // decides whether this is actually a success — see openProc.
        if (!root.attachmentIsImage) {
          root.openPending = true
          openProc.requestId = requestId
          openProc.command = ["xdg-open", parsed.path]
          openProc.running = true
        }
      } else {
        root.mediaPath = ""
        // wa-media always prints a JSON object with an `error` field on
        // refusal, even on a network failure — see DownloadFailed/
        // MediaRefused in bin/wa-media. Falling back to a generic string
        // only covers a response that could not be parsed at all (a crash
        // mid-write), the same category doSend()'s catch block handles.
        root.mediaError = (parsed && parsed.error)
            ? parsed.error : "no se pudo descargar el adjunto"
      }
    }
    mediaProc.running = true
  }

  // Its own Process too, loaded once and re-read on every refresh() —
  // labels can be renamed in accounts.json between refreshes, and this
  // is exactly how the panel is meant to find out: by asking, not by
  // trusting a copy the entry might carry.
  Process {
    id: accountsProc
    stdout: StdioCollector {
      onStreamFinished: {
        var parsed = null
        try { parsed = JSON.parse(text) } catch (e) { parsed = null }
        var map = {}
        if (Array.isArray(parsed)) {
          for (var i = 0; i < parsed.length; i++) {
            var a = parsed[i]
            if (a && a.id) map[a.id] = a.label || a.id
          }
        }
        root.accountLabels = map
      }
    }
  }

  function loadAccountLabels() {
    if (accountsProc.running)
      return
    accountsProc.command = [root.pluginDir + "bin/wa-reply", "accounts"]
    accountsProc.running = true
  }

  // Long quick-reply texts are truncated for the button label only — the
  // full text is still what gets sent (doSend() reads from quickReplies,
  // never from the label).
  function elideLabel(text, max) {
    max = max || 42
    return text.length > max ? text.slice(0, max - 1) + "…" : text
  }

  // Shared by "Enviar" and every quick-reply button: exactly one code path
  // reaches `wa-reply send`, so the timestamp check, the "unknown"
  // handling, the status bookkeeping and the queue removal all behave
  // identically no matter which button triggered it. A quick reply is not
  // a second send path — it is a different source of `text` for this one.
  function doSend(text) {
    if (root.inFlight || sendProc.running)
      return
    root.status = "enviando…"
    root.statusWarn = false
    root.inFlight = true
    var args = ["send", root.currentId, "--text", text]
    if (root.needsAck && root.acknowledged)
      args.push("--acknowledged")
    sendProc.command = [root.pluginDir + "bin/wa-reply"].concat(args)
    sendProc.done = function (out) {
      // Cleared first, before anything that can throw, so a malformed
      // response never leaves the buttons permanently disabled.
      root.inFlight = false
      try {
        const r = JSON.parse(out)
        if (r.ok && r.dry_run) {
          // Nothing was sent, so nothing changes: the entry stays in
          // the queue and stays open. Say so plainly.
          root.status = "DRY RUN — no se envió nada. "
                       + "La entrada sigue pendiente en la cola."
          root.statusWarn = false
          root.refresh()
        } else if (r.ok && r.superseded) {
          root.status = "enviado. Llegó un mensaje nuevo en "
                       + "este chat mientras redactabas, así que "
                       + "la entrada sigue en la cola."
          root.statusWarn = true
          root.refresh()
        } else if (r.ok) {
          root.status = "enviado"
          root.statusWarn = false
          root.select(null)
          root.refresh()
        } else if (r.needs_ack) {
          root.status = r.error
          root.statusWarn = true
          root.acknowledged = false
          root.refresh()
        } else if (r.unknown) {
          // The request timed out: the message may or may not have
          // gone out. wa-reply has recorded "unknown" on disk, so the
          // refresh below is what re-arms the block — it survives a
          // restart.
          root.status = "sin confirmación: puede que el mensaje ya se haya enviado — revisá WhatsApp antes de reintentar"
          root.statusWarn = true
          root.acknowledged = false
          root.refresh()
        } else {
          root.status = "falló: " + r.error
          root.statusWarn = true
          root.refresh()
        }
      } catch (e) {
        // A send whose response could not be parsed (wa-reply crashed
        // or was truncated mid-write) is exactly as ambiguous as an
        // `unknown` outcome — it may already have reached WhatsApp —
        // and unlike `unknown` we cannot rely on anything having been
        // written to disk, so it also gets the volatile block. Scoped
        // to the send path only.
        root.status = "sin confirmación: puede que el mensaje ya se haya enviado — revisá WhatsApp antes de reintentar"
        root.statusWarn = true
        root.sendBlocked = true
        root.refresh()
      }
    }
    sendProc.running = true
  }

  function select(entry) {
    root.current = entry
    root.currentId = entry ? entry.id : ""
    root.draft = ""
    root.idea = ""
    root.drafting = false
    root.status = ""
    root.statusWarn = false
    root.acknowledged = false
    root.sendBlocked = false
    // A download in flight (or its result) belongs to the entry that was
    // open when it started. Reselecting — even re-clicking the same
    // entry — must not leave a previous chat's image, error or spinner
    // showing under the newly opened one.
    root.mediaPath = ""
    root.mediaError = ""
    root.mediaLoading = false
    root.openPending = false
    root.mediaRequestId = ""
    // Every chat opens on the extract, never straight into the composer
    // — reading and writing are deliberately separate steps.
    root.composing = false
    // Quick replies are this entry's account's own list — reload for
    // whatever is now selected (or clear, for "back to the list").
    root.loadQuickReplies(root.currentId)
    root.loadThread(root.currentId)
  }

  // The extract's "Responder" button: the only path into the composer.
  // Guarded here too, not just by the button's own `enabled`, the same
  // belt-and-suspenders style `run()`/doSend() already use elsewhere in
  // this file — an entry left "unknown"/"sending" must not be reachable
  // by any route without the deliberate acknowledgement, and this is the
  // one new door this task adds to the composer.
  function openComposer() {
    if (root.needsAck && !root.acknowledged)
      return
    root.composing = true
    Qt.callLater(function() { composer.forceActiveFocus() })
  }

  // Steps back to the extract without dropping the selection — unlike
  // the extract's own Volver (root.select(null)), which goes all the way
  // back to the list. Used by the composer's Volver and by Escape there,
  // so leaving mid-draft never silently discards which chat was open.
  function backToExtract() {
    root.composing = false
    root.status = ""
    root.statusWarn = false
  }

  // The single composer box's one action: take whatever is currently
  // typed as the idea, ask `claude -p` (via `wa-reply draft`) to turn it
  // into a message, and — on success — replace the box's own content with
  // the drafted message. Used identically for the first draft and for a
  // redraft after "Redactar de nuevo" cleared the box back to idea mode,
  // so there is exactly one code path that can ever populate root.draft.
  function requestDraft(box) {
    if (root.drafting || root.inFlight)
      return
    var text = box.text.trim()
    if (text === "")
      return
    // Captured so a stray late response can't overwrite the composer if
    // the user has since switched to a different queued chat.
    var requestId = root.currentId
    root.status = ""
    root.statusWarn = false
    var started = root.run(["draft", requestId, "--idea", text], function (out) {
      root.drafting = false
      if (root.currentId !== requestId)
        return
      try {
        const d = JSON.parse(out)
        if (d.error) {
          root.status = "error: " + d.error
          root.statusWarn = true
        } else {
          root.idea = text
          root.draft = d.draft
          box.text = d.draft
          root.status = ""
          root.statusWarn = false
        }
      } catch (e) {
        root.status = "no se pudo redactar"
        root.statusWarn = true
      }
    })
    if (started)
      root.drafting = true
  }

  // "Redactar de nuevo": drop the current draft and hand the box back for
  // idea-entry, prefilled with the idea that produced it so it's a quick
  // edit rather than starting from a blank box. The guard against sending
  // a raw idea lives in the Enviar button (disabled while root.draft is
  // empty), which this restores by clearing root.draft.
  function redraftAgain(box) {
    if (root.drafting || root.inFlight)
      return
    box.text = root.idea
    root.draft = ""
    root.status = ""
    root.statusWarn = false
    Qt.callLater(function () { box.forceActiveFocus(); box.selectAll() })
  }

  function refresh() {
    root.loadAccountLabels()
    // Re-fetch for whatever is currently open — refresh() runs on every
    // panel open and on a timer-free basis after every send/draft, and a
    // rename or an account swap on the open entry must not leave a stale
    // list showing.
    root.loadQuickReplies(root.currentId)
    root.loadThread(root.currentId)
    run(["list"], function (out) {
      var list = []
      var parsed = null
      try { parsed = JSON.parse(out) } catch (e) { parsed = null }
      if (Array.isArray(parsed)) {
        list = parsed
        root.storeError = ""
      } else if (parsed && parsed.error) {
        // Broken install: `wa-reply list` refused to guess. Do not
        // fall back to an empty list silently — that is exactly
        // the bug this replaces (a permission error on queue.json
        // used to look identical to "no messages").
        list = []
        root.storeError = parsed.error
      } else {
        list = []
        root.storeError = "wa-reply list dio una respuesta inesperada"
      }
      root.entries = list
      // Re-resolve the open entry against what is now on disk, so the
      // detail view reflects the stored status (and any newer message
      // in the same chat) rather than the click-time snapshot.
      if (root.currentId !== "") {
        var found = null
        for (var i = 0; i < list.length; i++) {
          if (list[i].id === root.currentId) { found = list[i]; break }
        }
        root.current = found
        if (found === null)
          root.currentId = ""
      }
    })
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(Style.space(400))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // The detail view's own fields own input while it is open — the same
      // reasoning the network panel's inline passphrase prompt uses. Letting
      // the catcher's j/k/arrow bindings run underneath a TextField would
      // steal the keys the field needs. Settings has its own text fields
      // too (account id, number, tone, quick replies, the typed unlink
      // confirmation), so it blocks the catcher the same way.
      blocked: root.current !== null || root.settingsOpen
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.spacing.panelPadding
        spacing: Style.spacing.lg

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.md
          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            text: root.settingsOpen ? "Configuración"
                  : (root.current ? root.current.push_name : "Pendientes")
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }
          // The gear: toggles between the queue and settings. Hidden while
          // a chat is open in the queue view — leaving via "Volver" first
          // keeps there being exactly one way back to the list from there.
          Button {
            text: root.settingsOpen ? "Volver" : "⚙"
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            bordered: true
            visible: root.current === null
            onClicked: {
              root.settingsOpen = !root.settingsOpen
              if (root.settingsOpen && settingsView.onShown)
                settingsView.onShown()
              else if (!root.settingsOpen)
                root.refresh()
            }
          }
        }

        SettingsView {
          id: settingsView
          visible: root.settingsOpen
          Layout.fillWidth: true; Layout.fillHeight: true
          pluginDir: root.pluginDir
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
          urgent: root.contentUrgent
        }

        Rectangle {
          visible: !root.settingsOpen && root.storeError !== "" && !root.current
          Layout.fillWidth: true
          implicitHeight: storeErrorText.implicitHeight + Style.spacing.xxl
          color: Style.normalFillFor(root.contentUrgent, root.contentUrgent)
          radius: Style.cornerRadius
          border.width: Style.spacing.hairline
          border.color: root.contentUrgent
          Text {
            id: storeErrorText
            textFormat: Text.PlainText
            anchors.left: parent.left; anchors.right: parent.right
            anchors.top: parent.top; anchors.margins: Style.spacing.md
            text: "⚠ No se pudo leer la cola: " + root.storeError
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap
          }
        }

        ListView {
          visible: !root.settingsOpen && !root.current
          Layout.fillWidth: true; Layout.fillHeight: true
          model: root.entries
          spacing: Style.spacing.sm
          // The queue grows without bound as messages arrive. ListView is
          // already a Flickable, but without `clip` its rows simply drew
          // past the panel's fixed bounds instead of being cut off and
          // scrolled — this plus the attached ScrollBar (shown only while
          // there is more than fits, per the AsNeeded policy used
          // elsewhere in the shell) is what makes it actually scroll.
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
          delegate: Rectangle {
            id: row
            required property var modelData
            // An entry whose last send outcome is unknown must be
            // visible AS SUCH in the list, not only once it is opened.
            // "sending" is an entry whose wa-reply was killed mid-POST:
            // the same ambiguity as "unknown", so the same marker.
            readonly property bool ambiguous: row.modelData.status === "unknown"
                                               || row.modelData.status === "sending"
            width: ListView.view.width; height: Style.space(52)
            color: ma.containsMouse ? Style.hoverFillFor(root.contentForeground, Color.accent) : "transparent"
            radius: Style.cornerRadius
            border.color: row.ambiguous ? root.contentUrgent : "transparent"
            border.width: row.ambiguous ? Style.spacing.hairline : 0

            ColumnLayout {
              anchors.fill: parent; anchors.margins: Style.spacing.md; spacing: Style.spacing.xxs

              RowLayout {
                spacing: Style.spacing.md
                Text {
                  textFormat: Text.PlainText
                  text: row.modelData.push_name +
                        (row.modelData.count > 1
                         ? "  (" + row.modelData.count + ")" : "")
                  color: root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.subtitle
                }
                // The account badge: which number this conversation
                // belongs to. Same information the detail view states in
                // full ("sale desde …"), shown at a glance in the list so
                // the two numbers are never mixed up before opening a
                // chat.
                Rectangle {
                  radius: Style.cornerRadius
                  color: Style.normalFillFor(root.contentForeground, Color.accent)
                  implicitWidth: badgeText.implicitWidth + Style.spacing.md
                  implicitHeight: badgeText.implicitHeight + Style.spacing.xxs
                  Text {
                    id: badgeText
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: root.accountLabel(row.modelData.account)
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                }
                Text {
                  textFormat: Text.PlainText
                  visible: row.ambiguous
                  text: row.modelData.status === "sending"
                        ? "⚠ envío interrumpido" : "⚠ sin confirmar"
                  color: root.contentUrgent
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                }
                Text {
                  textFormat: Text.PlainText
                  visible: row.modelData.status === "failed"
                  text: "falló"
                  color: Color.accent
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }
              Text {
                textFormat: Text.PlainText
                text: row.modelData.text
                color: Color.muted
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.bodySmall
                elide: Text.ElideRight; Layout.fillWidth: true
              }
            }
            MouseArea {
              id: ma; anchors.fill: parent; hoverEnabled: true
              onClicked: root.select(row.modelData)
            }
          }
        }

        // States plainly which number the reply will leave by. Not a
        // choice — a reply always leaves by the number that received it,
        // there is no selector anywhere in this panel, and this line
        // exists so that fact is never left implicit. Shared by both the
        // extract and the composer (rather than living in just one of
        // them) so it survives regardless of which of the two screens is
        // showing.
        Text {
          textFormat: Text.PlainText
          visible: !root.settingsOpen && root.current !== null
          text: "La respuesta va a salir desde: "
                + root.accountLabel(root.current ? root.current.account : "")
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
          wrapMode: Text.Wrap; Layout.fillWidth: true
        }

        // Screen 1 of 2 for an open chat: the extract. Reading and writing
        // are deliberately separate steps (the owner's own request) — this
        // is as far as picking a chat goes on its own; only "Responder"
        // moves on to the composer below.
        ColumnLayout {
          visible: !root.settingsOpen && root.current !== null && !root.composing
          Layout.fillWidth: true; Layout.fillHeight: true
          spacing: Style.spacing.lg

          // The durable "unknown" warning, with the deliberate
          // acknowledgement that re-arms the send button. Lives here, on
          // the extract, rather than on the composer: this is the one new
          // door this task adds (there used to be only one screen after
          // picking a chat), and "Responder" below refuses to open that
          // door until this is acknowledged — see openComposer(). The
          // composer's own Enviar/quick-reply guards still check
          // needsAck/acknowledged too, unchanged, as a second, independent
          // check the same way every other guard in this file is doubled.
          Rectangle {
            visible: root.needsAck
            Layout.fillWidth: true
            implicitHeight: ackCol.implicitHeight + Style.spacing.xxl
            color: Style.normalFillFor(root.contentUrgent, root.contentUrgent)
            radius: Style.cornerRadius
            border.width: Style.spacing.hairline
            border.color: root.contentUrgent
            ColumnLayout {
              id: ackCol
              anchors.left: parent.left; anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.spacing.md
              spacing: Style.spacing.md
              Text {
                textFormat: Text.PlainText
                text: (root.current && root.current.status === "sending"
                       ? "⚠ Un envío anterior de esta entrada quedó interrumpido "
                         + "a mitad de camino (se cerró wa-reply antes de saber "
                         + "el resultado): "
                       : "⚠ El envío anterior quedó sin confirmación: ")
                      + "puede que este mensaje YA se haya enviado. Revisá "
                      + "WhatsApp antes de reintentar — si lo reenviás sin "
                      + "mirar, la persona puede recibirlo dos veces."
                color: root.contentForeground
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap; Layout.fillWidth: true
              }
              Button {
                visible: !root.acknowledged
                text: "Revisé WhatsApp, quiero reenviar"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                bordered: true
                onClicked: root.acknowledged = true
              }
              Text {
                textFormat: Text.PlainText
                visible: root.acknowledged
                text: "Reenvío habilitado para esta entrada."
                color: Color.accent
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            text: "Extracto de la conversación"
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }

          // The ring only holds what arrived since the plugin started
          // running, so an older conversation extracts thin or empty.
          // That is the NORMAL case, not a bug, and it has to say so —
          // a blank panel with no explanation reads as broken.
          Text {
            textFormat: Text.PlainText
            visible: root.threadEntries.length === 0 && root.threadError === ""
            text: "Todavía no hay nada guardado de este chat (o casi nada). "
                  + "El extracto solo muestra lo que llegó desde que el "
                  + "asistente empezó a correr — para una conversación más "
                  + "vieja esto es lo esperable, no una falla."
            color: Color.muted
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap; Layout.fillWidth: true
          }

          // An unexpected response shape from `wa-reply thread` — kept
          // apart from the empty-ring case above the same way storeError
          // is kept apart from a genuinely empty queue.
          Rectangle {
            visible: root.threadError !== ""
            Layout.fillWidth: true
            implicitHeight: threadErrorText.implicitHeight + Style.spacing.xxl
            color: Style.normalFillFor(root.contentUrgent, root.contentUrgent)
            radius: Style.cornerRadius
            border.width: Style.spacing.hairline
            border.color: root.contentUrgent
            Text {
              id: threadErrorText
              textFormat: Text.PlainText
              anchors.left: parent.left; anchors.right: parent.right
              anchors.top: parent.top; anchors.margins: Style.spacing.md
              text: "⚠ No se pudo leer el extracto: " + root.threadError
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
            }
          }

          // The extract itself: the stored ring, oldest first, each side
          // distinguishable by its own "direction" mark (never guessed
          // from the name — see build_prompt's own reasoning in
          // bin/wa-reply) via alignment and fill color. Scrollable like
          // everything else in this panel — the queue list and the
          // composer both already are.
          ListView {
            visible: root.threadEntries.length > 0
            Layout.fillWidth: true; Layout.fillHeight: true
            model: root.threadEntries
            spacing: Style.spacing.sm
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Item {
              id: bubbleRow
              required property var modelData
              readonly property bool outgoing: bubbleRow.modelData.direction === "out"
              width: ListView.view.width
              implicitHeight: bubble.implicitHeight

              Rectangle {
                id: bubble
                width: bubbleRow.width * 0.78
                anchors.right: bubbleRow.outgoing ? parent.right : undefined
                anchors.left: bubbleRow.outgoing ? undefined : parent.left
                implicitHeight: bubbleCol.implicitHeight + Style.spacing.lg
                radius: Style.cornerRadius
                color: bubbleRow.outgoing
                       ? Style.normalFillFor(root.contentForeground, Color.accent)
                       : Style.hoverFillFor(root.contentForeground, Color.accent)

                ColumnLayout {
                  id: bubbleCol
                  anchors.left: parent.left; anchors.right: parent.right
                  anchors.top: parent.top; anchors.margins: Style.spacing.md
                  spacing: Style.spacing.xxs
                  Text {
                    textFormat: Text.PlainText
                    text: bubbleRow.outgoing ? "Vos"
                          : (root.current ? root.current.push_name : "")
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                  }
                  Text {
                    textFormat: Text.PlainText
                    text: bubbleRow.modelData.text
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                  }
                }
              }
            }
          }

          // The attachment viewer: what this task adds. Only for the
          // entry actually pending a reply (root.current), never for a
          // ring bubble above — see currentAttachment's own comment.
          ColumnLayout {
            visible: root.hasAttachment
            Layout.fillWidth: true
            spacing: Style.spacing.sm

            Text {
              textFormat: Text.PlainText
              text: "Adjunto"
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }

            // Not downloadable: a location, a contact, or a malformed
            // event missing a field wa-media needs. Says so plainly —
            // no button, because no button here could ever work; the
            // spec calls out by name the bug of offering one anyway.
            Text {
              textFormat: Text.PlainText
              visible: !root.attachmentDownloadable
              text: "Este mensaje trae "
                    + root.attachmentTypeLabel(root.currentAttachment
                                                ? root.currentAttachment.type : "")
                    + " y no se puede ver desde acá: no hay forma de "
                    + "pedírselo a WhatsApp para este tipo de adjunto."
              color: Color.muted
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap; Layout.fillWidth: true
            }

            // Downloadable and not yet shown/opened: the button that
            // starts the fetch, relabelled to show it is working and to
            // offer a retry after a failure — never a silent dead end.
            RowLayout {
              visible: root.attachmentDownloadable && root.mediaPath === ""
              spacing: Style.spacing.md
              Button {
                text: root.mediaLoading ? "Descargando…"
                      : (root.mediaError !== "" ? "Reintentar"
                         : (root.attachmentIsImage ? "Ver imagen" : "Abrir"))
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                bordered: true
                // Disabled only while a download is actually running —
                // the rest of the panel (the list, Volver, the gear)
                // stays usable the whole time, since this is its own
                // Process and its own guard, not a shared one.
                enabled: !root.mediaLoading
                onClicked: root.fetchMedia()
              }
            }

            // A failed download: said plainly, with the retry button
            // above still live — this is the case the spec names as the
            // one that must never look like an empty result.
            Text {
              textFormat: Text.PlainText
              visible: root.mediaError !== ""
              text: "⚠ " + root.mediaError
              color: root.contentUrgent
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap; Layout.fillWidth: true
            }

            // Audio/video/document: xdg-open launched the moment the
            // download finished (see fetchMedia()), but "launched" is not
            // "succeeded" — this waits for its own exit code (openProc's
            // onExited) before claiming it worked. A failure there clears
            // mediaPath and shows mediaError instead (the Text above),
            // re-offering the button as a retry, so this line is only
            // ever true when it is actually true.
            Text {
              textFormat: Text.PlainText
              visible: root.openPending
              text: "Abriendo con el visor del sistema…"
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Text {
              textFormat: Text.PlainText
              visible: root.mediaPath !== "" && !root.attachmentIsImage
                       && !root.openPending
              text: "Se abrió con el visor del sistema."
              color: Color.accent
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
            }

            // Images and stickers: inline, bounded to the panel's own
            // width so a large photo fits rather than blowing the layout
            // out — `mediaBox`'s own height is capped too, and the whole
            // extract screen is already inside a scrollable arrangement
            // (this ColumnLayout sits above the Volver/Responder row,
            // and the ring ListView above it already scrolls; a very
            // tall image plus a long ring simply makes more of the
            // extract need scrolling, which is the point).
            Item {
              id: mediaBox
              visible: root.mediaPath !== "" && root.attachmentIsImage
                       && mediaImage.status !== Image.Error
              Layout.fillWidth: true
              implicitHeight: (mediaImage.status === Image.Ready
                               && mediaImage.sourceSize.width > 0)
                  ? Math.min(mediaBox.width * mediaImage.sourceSize.height
                             / mediaImage.sourceSize.width, Style.space(480))
                  : Style.space(160)

              Image {
                id: mediaImage
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                source: root.mediaPath ? ("file://" + root.mediaPath) : ""
                // The cache prunes itself (bin/wa-media's own docstring:
                // age first, then size, oldest first) — a file that was
                // downloaded successfully a while ago can be gone by the
                // time this Image tries to load it. Image.Error is the
                // one signal QML gives for exactly that (or for a file
                // that was never a real image), and it must degrade to
                // an honest message, never a broken-image placeholder
                // with nothing said about why.
                onStatusChanged: {
                  if (status === Image.Error) {
                    root.mediaError = "el archivo ya no está en la caché "
                        + "(puede haberse podado) o no se pudo mostrar como "
                        + "imagen. Probá de nuevo."
                    root.mediaPath = ""
                  }
                }
              }
            }
          }

          RowLayout {
            spacing: Style.spacing.lg
            Button {
              text: "Volver"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              onClicked: root.select(null)
            }
            Button {
              text: "Responder"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              // Same condition openComposer() itself enforces — the
              // button being disabled is the visible half of the guard,
              // the function's own early return is the half nothing can
              // route around.
              enabled: !root.needsAck || root.acknowledged
              onClicked: root.openComposer()
            }
          }
        }

        // Screen 2 of 2: the composer. Reached only through "Responder"
        // above.
        ColumnLayout {
          visible: !root.settingsOpen && root.current !== null && root.composing
          Layout.fillWidth: true; Layout.fillHeight: true
          spacing: Style.spacing.lg

          // One-click quick replies. Visually separated from the composer
          // (its own heading, its own divider) because clicking one of
          // these does NOT fill the box below — it sends the text
          // immediately, with no further confirmation. The heading says
          // that plainly so nobody expecting "fill the composer" gets
          // "message delivered" instead. Disabled while a send or a
          // draft is running (inFlight/drafting) so a double click can't
          // race the composer's own send, while needsAck applies and is
          // not yet acknowledged, and while sendBlocked is set — the same
          // four conditions the "Enviar" button checks, and the first
          // three of which one `wa-reply send` re-enforces server-side
          // regardless of this button's enabled state.
          //
          // sendBlocked is the one that is NOT re-enforced server-side:
          // it exists precisely because a send's response could not be
          // parsed, so nothing on disk can be trusted about whether the
          // message arrived, and wa-reply has no stored status to refuse
          // on. These buttons send immediately, with no confirmation
          // step, so omitting it here was a one-click double send to a
          // real person. Keep this list and Enviar's `enabled` in step.
          ColumnLayout {
            visible: root.quickReplies.length > 0
            Layout.fillWidth: true
            spacing: Style.spacing.xs
            Text {
              textFormat: Text.PlainText
              text: "Respuestas rápidas — envían al toque, sin confirmar"
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }
            Flow {
              Layout.fillWidth: true
              spacing: Style.spacing.sm
              Repeater {
                model: root.quickReplies
                delegate: Button {
                  required property string modelData
                  text: root.elideLabel(modelData)
                  foreground: root.contentForeground
                  fontFamily: root.contentFontFamily
                  bordered: true
                  enabled: !root.sendBlocked
                           && !root.inFlight && !root.drafting
                           && (!root.needsAck || root.acknowledged)
                  onClicked: root.doSend(modelData)
                }
              }
            }
            Rectangle {
              Layout.fillWidth: true
              Layout.preferredHeight: Style.spacing.hairline
              color: Color.muted
            }
          }

          // Once a draft exists, the composer below no longer holds the
          // user's raw idea — it holds the message that Enviar will
          // actually send. This heading plus the muted recap line are the
          // only cue for that switch, so they have to say it plainly.
          ColumnLayout {
            visible: root.draft !== ""
            Layout.fillWidth: true
            spacing: Style.spacing.xxs
            Text {
              textFormat: Text.PlainText
              text: "Mensaje a enviar"
              color: root.contentForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.spacing.md
              Text {
                textFormat: Text.PlainText
                text: "Idea: " + root.idea
                color: Color.muted
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.bodySmall
                elide: Text.ElideRight
                Layout.fillWidth: true
              }
              Button {
                text: "Redactar de nuevo"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                bordered: true
                enabled: !root.drafting && !root.inFlight
                onClicked: root.redraftAgain(composer)
              }
            }
          }

          // The single composer box: idea-entry before a draft exists,
          // then the editable drafted message. Whichever role it is
          // playing, its own text is what requestDraft() reads from or
          // Enviar sends — there is no separate field a raw idea could
          // hide in.
          // The reported bug: `claude -p` can easily draft a reply longer
          // than this box's visible area, and with a plain TextArea the
          // rest was simply unreachable — the text existed, nobody could
          // scroll to it. ScrollView is the documented way to add
          // scrolling to a TextArea (it treats the TextArea as its own
          // flickable content and adds a scrollbar around it) and changes
          // nothing else: `composer` is still the TextArea's own id, so
          // every `composer.forceActiveFocus()` / `.selectAll()` /
          // `.text` reference elsewhere (select(), redraftAgain(),
          // requestDraft(), doSend(), the Enviar guard) keeps working
          // unchanged. No wheel is stolen from the queue ListView either:
          // this whole detail view and the list are mutually exclusive via
          // `visible`, so they are never on screen — or under the mouse —
          // at the same time.
          ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            TextArea {
              id: composer
              placeholderText: "Qué querés responder…"
              color: root.contentForeground
              wrapMode: TextEdit.Wrap
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.body
              selectByMouse: true
              enabled: !root.drafting && !root.inFlight
              // Steps back to the extract, not all the way to the list —
              // see backToExtract(). The selection (and any draft in
              // progress) survives; only the list's Volver drops it.
              Keys.onEscapePressed: root.backToExtract()
              // Enter drafts the idea currently in the box, exactly like the
              // Redactar button — but only while the box is still holding an
              // idea (no draft yet). Once a draft exists this is an ordinary
              // multi-line editable message, and Enter has to behave like an
              // ordinary newline instead of silently re-drafting whatever the
              // user is mid-edit typing.
              Keys.onReturnPressed: (event) => {
                if (root.draft !== "" || root.drafting || root.inFlight)
                  return
                event.accepted = true
                root.requestDraft(composer)
              }
              Keys.onEnterPressed: (event) => {
                if (root.draft !== "" || root.drafting || root.inFlight)
                  return
                event.accepted = true
                root.requestDraft(composer)
              }
              background: Rectangle {
                color: Style.normalFillFor(root.contentForeground, Color.accent)
                radius: Style.cornerRadius
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            text: root.status
            color: root.statusWarn ? root.contentUrgent : Color.accent
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap
            Layout.fillWidth: true
          }

          RowLayout {
            spacing: Style.spacing.lg
            // Before a draft exists, the box could hold either a raw idea
            // for Claude or the person's own finished message — the code
            // cannot tell those apart, so the two actions are offered
            // side by side and the person states which one this is.
            // "Redactar" (unchanged): sends the typed text to `claude -p`
            // and replaces the box with the draft. Enter is the shortcut
            // for this same action, so neither is the only way to
            // trigger it.
            Button {
              text: root.drafting ? "Redactando…" : "Redactar"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              visible: root.draft === ""
              enabled: !root.drafting && !root.inFlight
                       && composer.text.trim().length > 0
              onClicked: root.requestDraft(composer)
            }
            // "Enviar tal cual": the direct path this task adds. Sends
            // exactly what is typed, with no drafting step in between —
            // for the case where what the person typed already is the
            // message. Goes through the exact same doSend()/sendProc as
            // every other send in this file, and carries every guard
            // "Enviar" itself carries below (needsAck/acknowledged,
            // sendBlocked, drafting, inFlight, non-blank text): this is a
            // second door onto the one send path, not a second send path.
            Button {
              text: "Enviar tal cual"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              visible: root.draft === ""
              enabled: composer.text.trim().length > 0
                       && !root.sendBlocked
                       && !root.drafting
                       && !root.inFlight
                       && (!root.needsAck || root.acknowledged)
              onClicked: root.doSend(composer.text)
            }
            // Once a draft exists the distinction above collapses — the
            // box holds a real message either way — so this is simply
            // "Enviar", the same button/guards as always, just no longer
            // needing the word "tal cual" to disambiguate it from a
            // drafting action that isn't offered here anymore.
            Button {
              text: "Enviar"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              visible: root.draft !== ""
              enabled: composer.text.trim().length > 0
                       && !root.sendBlocked
                       && !root.drafting
                       && !root.inFlight
                       && (!root.needsAck || root.acknowledged)
              onClicked: root.doSend(composer.text)
            }
            Button {
              text: "Volver"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              enabled: !root.inFlight && !root.drafting
              onClicked: root.backToExtract()
            }
          }
        }
      }
    }
  }
}
