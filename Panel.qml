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

  // account id -> label, via `wa-reply accounts`. The queue entry carries
  // only the account ID, never a label: a label lives exclusively in
  // accounts.json, so a rename there can never leave a stale copy
  // disagreeing with it somewhere in the queue. This is asked for fresh
  // on every refresh() rather than cached from the entry, through its
  // own Process for the same reason quick replies get one — it must
  // never race list/draft/send.
  property var accountLabels: ({})

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
    // Quick replies are this entry's account's own list — reload for
    // whatever is now selected (or clear, for "back to the list").
    root.loadQuickReplies(root.currentId)
    if (entry) Qt.callLater(function() { composer.forceActiveFocus() })
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

        ColumnLayout {
          visible: !root.settingsOpen && root.current !== null
          Layout.fillWidth: true; Layout.fillHeight: true
          spacing: Style.spacing.lg

          // States plainly which number the reply will leave by. Not a
          // choice — a reply always leaves by the number that received
          // it, there is no selector anywhere in this panel, and this
          // line exists so that fact is never left implicit.
          Text {
            textFormat: Text.PlainText
            visible: root.current !== null
            text: "La respuesta va a salir desde: "
                  + root.accountLabel(root.current ? root.current.account : "")
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: true
            wrapMode: Text.Wrap; Layout.fillWidth: true
          }

          // Capped and scrollable: a long incoming WhatsApp message has
          // exactly the same problem as a long draft below — it can run
          // past the visible area on its own, and it must not be allowed
          // to eat the room the composer needs. The cap only kicks in once
          // the text is actually taller than it; short messages get no
          // scrollbar at all (ScrollBar.AsNeeded).
          ScrollView {
            id: incomingScroll
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(incomingText.implicitHeight, Style.space(140))
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            Text {
              id: incomingText
              width: incomingScroll.availableWidth
              textFormat: Text.PlainText
              text: root.current ? root.current.text : ""
              color: Color.muted
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.Wrap
            }
          }

          // The durable "unknown" warning, with the deliberate
          // acknowledgement that re-arms the send button.
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
              Keys.onEscapePressed: root.select(null)
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
            // Visible only while the composer is still holding a raw
            // idea (no draft yet). Enter is the shortcut for the exact
            // same action, so neither is the only way to trigger it.
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
            Button {
              text: "Enviar"
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              bordered: true
              // Disabled until a draft exists: with a single composer
              // box, the user's raw idea sits in this same field before
              // drafting, so this guard is what stops "decile que estoy
              // ocupado" from ever going out verbatim.
              enabled: root.draft !== ""
                       && composer.text.trim().length > 0
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
              onClicked: root.select(null)
            }
          }
        }
      }
    }
  }
}
