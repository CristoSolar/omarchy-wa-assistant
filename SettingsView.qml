// SettingsView.qml
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// The settings screen: list accounts, link a new one, unlink one behind a
// typed confirmation, and edit each account's tone and quick replies.
//
// Embedded as a plain child of ChatWindow.qml, which the shell destroys
// whenever the window closes (Esc, ✕, the bar button) — and every Process
// in it is killed with it. A pairing takes minutes and the person may well
// close the window while it waits; a wa-pair or wa-unlink killed halfway
// can leave the wuzapi user, accounts.json and secrets.env out of step. So
// neither runs as a child of this view: `detached()` starts each one in its
// own transient systemd user unit (systemd-run --user --collect), which
// outlives the window and even a shell restart, with its --json event
// stream going to a file under the state root (link.jsonl / unlink.jsonl).
// This view only reads that file back, polling it once a second while the
// run is alive, and replays it from the top on every read. When the window
// is opened again, Component.onCompleted finds the file and picks the run
// up where it is — still going, or finished while nobody was looking. The
// file is emptied once its result has been shown and the person moves on.
//
// This view owns its OWN Processes for everything it does. It never calls
// into ChatWindow.qml's `doSend()` or any of its Process ids — that is
// the boundary that makes "the settings screen must not be able to send a
// message" true by construction rather than by discipline: there is no
// send path here at all, only wa-pair/wa-unlink/wa-reply's read/write
// subcommands for tone and quick replies.
ColumnLayout {
  id: root
  spacing: Style.spacing.lg

  property string pluginDir: ""
  // Where link.jsonl / unlink.jsonl live; ChatWindow.qml passes its own.
  property string stateRoot: ""
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property color urgent: Color.urgent

  // list | link | unlink | tone | quick
  property string mode: "list"

  property var accounts: []
  property string accountsError: ""

  function refreshAccounts() {
    if (listAccountsProc.running)
      return
    listAccountsProc.command = [root.pluginDir + "bin/wa-reply", "accounts"]
    listAccountsProc.running = true
  }

  // Called by ChatWindow.qml whenever the gear opens this screen. Deliberately
  // does NOT reset `mode` while something is still IN PROGRESS — a link or
  // unlink mid-flight must keep showing its own progress when the person
  // reopens settings, not snap back to the account list and lose their
  // place.
  //
  // A FINISHED one is the opposite case: reopening the gear after a
  // successful unlink used to re-show "Cuenta desvinculada." — a stale
  // claim about something that happened a while ago, sitting where the
  // current state of the accounts should be, with no hint that it is
  // history rather than news. Once the result has been shown and the
  // screen closed, the screen has done its job; the next open starts from
  // the list, freshly read.
  //
  // A run that finished while this screen was not on view (the window
  // closed, or the person in a chat) shows its result once on the next
  // open instead: *ResultShown records that it has been seen.
  function onShown() {
    if (root.mode === "unlink" && root.unlinkDone) {
      if (root.unlinkResultShown) root.mode = "list"
      else root.unlinkResultShown = true
    } else if (root.mode === "link" && root.linkFinished()) {
      if (root.linkResultShown) root.mode = "list"
      else root.linkResultShown = true
    }
    if (root.mode === "list") {
      root.refreshAccounts()
      root.fetchMediaCacheSize()
    }
  }

  Component.onCompleted: {
    root.refreshAccounts()
    root.fetchMediaCacheSize()
    root.recoverRuns()
  }

  // -------------------------------------------------------------------
  // Detached runs (see the header). One fixed unit name per script, so a
  // second link or unlink cannot start while one is alive: systemd-run
  // refuses a unit that already exists.
  // -------------------------------------------------------------------

  readonly property string linkLog: root.stateRoot + "/link.jsonl"
  readonly property string unlinkLog: root.stateRoot + "/unlink.jsonl"

  function detached(unit, log, argv) {
    var cmd = ["systemd-run", "--user", "--collect", "--quiet", "--unit=" + unit,
               "-p", "StandardOutput=truncate:" + log, "-p", "StandardError=journal"]
    // The unit starts from the user manager's environment, not the
    // shell's: carry over the state overrides this window itself honors.
    var vars = ["WA_STATE_ROOT", "WA_DATA_DIR", "WA_SECRETS_FILE"]
    for (var i = 0; i < vars.length; i++) {
      var v = Quickshell.env(vars[i])
      if (v) cmd.push("--setenv=" + vars[i] + "=" + v)
    }
    return cmd.concat(["--"], argv)
  }

  // One probe answers both questions at once, in this order: is the unit
  // still alive, then what has it written. Checking first means a "gone"
  // answer always comes with the complete file (the run has exited).
  function probeCommand(unit, log) {
    return ["sh", "-c", 'if systemctl --user is-active --quiet "$1"; then echo active; '
            + 'else echo gone; fi; cat "$2" 2>/dev/null', "sh", unit, log]
  }

  // The window was (re)opened: pick up a run that a previous window
  // started, still going or finished while nobody was looking.
  function recoverRuns() {
    root.unlinkRecovering = true
    unlinkProbe.running = true
    root.linkRecovering = true
    linkProbe.running = true
  }

  // Leaving a finished run's screen is what "the result was seen" means;
  // the file is emptied so the next window does not bring it back.
  onModeChanged: {
    if (root.mode !== "link" && !root.linkRunning && root.linkOwnsLog) {
      Quickshell.execDetached(["truncate", "-s", "0", root.linkLog])
      root.linkOwnsLog = false
    }
    if (root.mode !== "unlink" && !root.unlinkRunning && root.unlinkOwnsLog) {
      Quickshell.execDetached(["truncate", "-s", "0", root.unlinkLog])
      root.unlinkOwnsLog = false
    }
  }

  // -------------------------------------------------------------------
  // Media cache — the caché de adjuntos the 2026-09-23 media-viewing plan
  // adds (bin/wa-media). Size and clearing both go through wa-media's own
  // subcommands (`size`, `clear`) — never a path or an env var name
  // reconstructed here. That is the whole point: this screen must stay
  // unable to know anything about where state lives that the scripts
  // themselves do not tell it, the same boundary that already keeps it
  // unable to send a message (see this file's own header). A hardcoded
  // "${WA_STATE_ROOT:-$HOME/...}/media" guess would silently drift out of
  // sync with sink/store.py's state_root() the day either one changes,
  // and report a reassuring zero over a directory nobody is writing to.
  // -------------------------------------------------------------------

  property real mediaCacheBytes: -1
  property string mediaCacheError: ""
  property bool mediaClearBusy: false
  property string mediaClearStatus: ""
  property bool mediaClearStatusWarn: false

  // Bytes, human-scaled. Deliberately coarse (one decimal, binary units)
  // — this is "does it need clearing", not an audit figure.
  function formatBytes(n) {
    if (n < 1024) return n + " B"
    var units = ["KiB", "MiB", "GiB", "TiB"]
    var v = n / 1024
    var i = 0
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
    return v.toFixed(1) + " " + units[i]
  }

  function fetchMediaCacheSize() {
    if (mediaSizeProc.running)
      return
    root.mediaCacheError = ""
    mediaSizeTerminalSeen = false
    mediaSizeProc.command = [root.pluginDir + "bin/wa-media", "size"]
    mediaSizeProc.running = true
  }

  // Set only once a usable answer (parsed JSON, either shape) actually
  // arrives. Checked the same way linkProbe and unlinkProbe check the
  // link/unlink terminal-event flags: a process that dies
  // without ever printing something this handler could parse — killed,
  // crashed before writing a line — must not leave the size silently
  // wherever it last was; it has to say plainly that nothing was learned.
  property bool mediaSizeTerminalSeen: false

  Process {
    id: mediaSizeProc
    stdout: StdioCollector {
      onStreamFinished: {
        var r = null
        try { r = JSON.parse(text) } catch (e) { r = null }
        if (r && r.ok && typeof r.bytes === "number") {
          root.mediaSizeTerminalSeen = true
          root.mediaCacheBytes = r.bytes
          root.mediaCacheError = ""
        } else if (r && r.ok === false) {
          root.mediaSizeTerminalSeen = true
          root.mediaCacheBytes = -1
          root.mediaCacheError = r.error || "no se pudo calcular el tamaño de la caché"
        }
        // Neither shape: leave mediaSizeTerminalSeen false and let
        // onExited below report it — the same "no terminal event" case
        // the link and unlink runs already handle in this file.
      }
    }
    onExited: function (exitCode) {
      if (!root.mediaSizeTerminalSeen) {
        root.mediaCacheBytes = -1
        root.mediaCacheError = "wa-media size terminó (código " + exitCode
          + ") sin responder; no se sabe cuánto ocupa la caché."
      }
    }
  }

  function clearMediaCache() {
    if (mediaClearProc.running)
      return
    root.mediaClearBusy = true
    root.mediaClearStatus = ""
    mediaClearProc.command = [root.pluginDir + "bin/wa-media", "clear"]
    mediaClearProc.running = true
  }

  Process {
    id: mediaClearProc
    stdout: StdioCollector {
      onStreamFinished: {
        root.mediaClearBusy = false
        var r = null
        try { r = JSON.parse(text) } catch (e) { r = null }
        if (r && r.ok) {
          root.mediaClearStatus = "caché vaciada"
          root.mediaClearStatusWarn = false
          root.fetchMediaCacheSize()
        } else {
          root.mediaClearStatus = (r && r.error) ? r.error : "no se pudo vaciar la caché"
          root.mediaClearStatusWarn = true
        }
      }
    }
  }

  Process {
    id: listAccountsProc
    stdout: StdioCollector {
      onStreamFinished: {
        var parsed = null
        try { parsed = JSON.parse(text) } catch (e) { parsed = null }
        if (Array.isArray(parsed)) {
          root.accounts = parsed
          root.accountsError = ""
        } else {
          root.accounts = []
          root.accountsError = (parsed && parsed.error)
            ? parsed.error : "wa-reply accounts dio una respuesta inesperada"
        }
      }
    }
  }

  // -------------------------------------------------------------------
  // Account list
  // -------------------------------------------------------------------

  ColumnLayout {
    visible: root.mode === "list"
    Layout.fillWidth: true; Layout.fillHeight: true
    spacing: Style.spacing.md

    Text {
      textFormat: Text.PlainText
      visible: root.accountsError !== ""
      text: "⚠ " + root.accountsError
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.Wrap
      Layout.fillWidth: true
    }

    // The media cache: what the 2026-09-23 media-viewing plan requires
    // in settings — how much it holds, and a way to empty it in one
    // step. Whole-cache only (bin/wa-media clear, no --account): a
    // single global control, not one per account.
    Rectangle {
      Layout.fillWidth: true
      implicitHeight: cacheCol.implicitHeight + Style.spacing.xxl
      color: Style.normalFillFor(root.foreground, Color.accent)
      radius: Style.cornerRadius
      ColumnLayout {
        id: cacheCol
        anchors.left: parent.left; anchors.right: parent.right
        anchors.top: parent.top; anchors.margins: Style.spacing.md
        spacing: Style.spacing.sm
        Text {
          textFormat: Text.PlainText
          text: "Caché de adjuntos"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }
        Text {
          textFormat: Text.PlainText
          text: root.mediaCacheError !== "" ? ("⚠ " + root.mediaCacheError)
                : root.mediaCacheBytes < 0 ? "Calculando…"
                : "Ocupa " + root.formatBytes(root.mediaCacheBytes) + " en disco."
          color: root.mediaCacheError !== "" ? root.urgent : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap; Layout.fillWidth: true
        }
        RowLayout {
          spacing: Style.spacing.md
          Button {
            text: root.mediaClearBusy ? "Vaciando…" : "Vaciar caché"
            foreground: root.foreground
            fontFamily: root.fontFamily
            bordered: true
            enabled: !root.mediaClearBusy
            onClicked: root.clearMediaCache()
          }
        }
        Text {
          textFormat: Text.PlainText
          visible: root.mediaClearStatus !== ""
          text: root.mediaClearStatus
          color: root.mediaClearStatusWarn ? root.urgent : Color.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap; Layout.fillWidth: true
        }
      }
    }

    ListView {
      Layout.fillWidth: true; Layout.fillHeight: true
      model: root.accounts
      spacing: Style.spacing.sm
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
      delegate: Rectangle {
        id: accRow
        required property var modelData
        width: ListView.view.width
        height: accCol.implicitHeight + Style.spacing.lg
        color: "transparent"

        ColumnLayout {
          id: accCol
          anchors.left: parent.left; anchors.right: parent.right
          anchors.top: parent.top; anchors.margins: Style.spacing.sm
          spacing: Style.spacing.xs

          Text {
            textFormat: Text.PlainText
            text: accRow.modelData.label + "  (" + accRow.modelData.id + ")"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }
          RowLayout {
            spacing: Style.spacing.sm
            Button {
              text: "Tono"
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.openTone(accRow.modelData.id)
            }
            Button {
              text: "Respuestas rápidas"
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.openQuick(accRow.modelData.id)
            }
            Button {
              text: "Desvincular"
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.openUnlink(accRow.modelData.id)
            }
          }
        }
      }
    }

    Button {
      text: "+ Vincular cuenta nueva"
      foreground: root.foreground
      fontFamily: root.fontFamily
      bordered: true
      onClicked: root.openLink()
    }
  }

  // -------------------------------------------------------------------
  // Linking — the centrepiece. `wa-pair --json` emits one JSON object per
  // line over several minutes into link.jsonl; linkPoll re-reads and
  // replays the file every second, which is what makes the code show up while the run is
  // still happening instead of only once it is long gone.
  // -------------------------------------------------------------------

  property string linkAccountId: ""
  property string linkNumber: ""
  // form | validating | code | linked
  // | done | error | no_terminal
  property string linkStage: "form"
  property string linkCode: ""
  property string linkError: ""
  // Set the moment a "done" or "error" event is actually parsed. Checked
  // once the unit is gone (linkProbe): if it is still false, the stream
  // ended with no terminal event — the script's own header says a SIGKILL
  // cannot run its EXIT trap, so this is a real outcome the UI must show
  // plainly, not a bug to paper over with a spinner.
  property bool linkTerminalSeen: false
  // The omarchy-wa-pair unit is (or may be) alive: linkPoll runs.
  property bool linkRunning: false
  property bool linkResultShown: false
  // link.jsonl holds a run this view started or recovered (never clear a
  // file some other run is writing).
  property bool linkOwnsLog: false
  property bool linkRecovering: false

  function linkFinished() {
    return root.linkStage === "done" || root.linkStage === "error"
      || root.linkStage === "no_terminal"
  }

  function openLink() {
    root.mode = "link"
    if (root.linkRunning)
      return  // show the run that is going, never a fresh form over it
    root.linkStage = "form"
    root.linkAccountId = ""
    root.linkNumber = ""
    root.linkCode = ""
    root.linkError = ""
  }

  function startLink() {
    if (root.linkRunning || root.linkRecovering || pairLaunch.running)
      return
    var id = root.linkAccountId.trim()
    var num = root.linkNumber.trim()
    if (id === "" || num === "")
      return
    root.linkStage = "validating"
    root.linkCode = ""
    root.linkError = ""
    root.linkTerminalSeen = false
    root.linkResultShown = false
    pairLaunch.command = root.detached("omarchy-wa-pair", root.linkLog, [root.pluginDir + "bin/wa-pair", id, num, "--json"])
    pairLaunch.running = true
  }

  // Rebuild the whole link state from link.jsonl, from the top.
  function replayLink(lines) {
    root.linkStage = "validating"
    root.linkCode = ""
    root.linkError = ""
    root.linkTerminalSeen = false
    for (var i = 0; i < lines.length; i++)
      root.handlePairLine(lines[i])
  }

  function linkEnded() {
    root.linkRunning = false
    if (root.visible) root.linkResultShown = true
  }

  function handlePairLine(line) {
    var s = String(line).trim()
    if (s === "")
      return
    var obj = null
    try { obj = JSON.parse(s) } catch (e) { return }
    if (!obj || !obj.event)
      return
    switch (obj.event) {
    case "validating":
      root.linkStage = "validating"
      if (obj.account) root.linkAccountId = obj.account
      break
    case "code":
      root.linkCode = obj.code || ""
      root.linkStage = "code"
      break
    case "linked":
      root.linkStage = "linked"
      break
    case "done":
      root.linkTerminalSeen = true
      root.linkStage = "done"
      root.refreshAccounts()
      break
    case "error":
      root.linkTerminalSeen = true
      root.linkStage = "error"
      root.linkError = obj.reason || "error desconocido"
      break
    }
  }

  // systemd-run returns as soon as the unit has started; the run itself
  // is then followed through link.jsonl.
  Process {
    id: pairLaunch
    stderr: StdioCollector { id: pairLaunchErr }
    onExited: function (exitCode) {
      if (exitCode === 0) {
        root.linkOwnsLog = true
        root.linkRunning = true
        return
      }
      root.linkStage = "error"
      root.linkTerminalSeen = true
      root.linkError = "No se pudo iniciar wa-pair (systemd-run, código " + exitCode
        + "). ¿Ya hay una vinculación en curso? " + String(pairLaunchErr.text || "").trim()
      root.linkEnded()
    }
  }

  Timer {
    id: linkPoll
    interval: 1000; repeat: true
    running: root.linkRunning
    onTriggered: if (!linkProbe.running) linkProbe.running = true
  }

  Process {
    id: linkProbe
    command: root.probeCommand("omarchy-wa-pair.service", root.linkLog)
    stdout: StdioCollector { onStreamFinished: root.onLinkProbe(text) }
  }

  function onLinkProbe(text) {
    var lines = String(text || "").split("\n")
    var active = lines.shift() === "active"
    if (root.linkRecovering) {
      root.linkRecovering = false
      if (!active && lines.join("").trim() === "")
        return  // nothing to pick up
      root.linkOwnsLog = true
      root.linkResultShown = false
      root.mode = "link"
    } else if (!root.linkRunning) {
      return
    }
    root.replayLink(lines)
    if (!root.linkTerminalSeen && active) {
      root.linkRunning = true
      return
    }
    if (!root.linkTerminalSeen) {
      root.linkStage = "no_terminal"
      root.linkError = "wa-pair terminó sin "
        + "confirmar el resultado. Puede haber sido interrumpido a la "
        + "fuerza. No se sabe si la cuenta quedó vinculada — revisá "
        + "manualmente antes de reintentar (por ejemplo, mirando si la "
        + "cuenta aparece en la lista, o los logs del contenedor)."
    }
    root.linkEnded()
  }

  ColumnLayout {
    visible: root.mode === "link"
    Layout.fillWidth: true; Layout.fillHeight: true
    spacing: Style.spacing.lg

    Text {
      textFormat: Text.PlainText
      text: "Vincular cuenta nueva"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.subtitle
      font.bold: true
    }

    ColumnLayout {
      visible: root.linkStage === "form"
      spacing: Style.spacing.sm
      Text {
        textFormat: Text.PlainText
        text: "Id de cuenta (minúsculas, números, guión bajo)"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      TextField {
        id: linkIdField
        Layout.fillWidth: true
        foreground: root.foreground
        text: root.linkAccountId
        onTextChanged: root.linkAccountId = text
      }
      Text {
        textFormat: Text.PlainText
        text: "Número, formato internacional, sin +"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      TextField {
        id: linkNumField
        Layout.fillWidth: true
        foreground: root.foreground
        text: root.linkNumber
        onTextChanged: root.linkNumber = text
      }
      RowLayout {
        spacing: Style.spacing.md
        Button {
          text: "Vincular"
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          enabled: root.linkAccountId.trim() !== "" && root.linkNumber.trim() !== ""
          onClicked: root.startLink()
        }
        Button {
          text: "Volver"
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          onClicked: root.mode = "list"
        }
      }
    }

    // The code, the stage text and the closing line can all be visible at
    // once by the time linking reaches "done", and the error comes from
    // wa-pair rather than from a short fixed string — length is not
    // guaranteed. Wrapped in ScrollView, same idiom as the composer.
    ScrollView {
      id: linkProgressScroll
      visible: root.linkStage !== "form"
      Layout.fillWidth: true; Layout.fillHeight: true
      clip: true
      ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
      ScrollBar.vertical.policy: ScrollBar.AsNeeded

    ColumnLayout {
      width: linkProgressScroll.availableWidth
      spacing: Style.spacing.md

      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "validating"
        text: "Validando cuenta…"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      // The code: large and legible, because this is what the person is
      // squinting at while holding their phone.
      Text {
        textFormat: Text.PlainText
        visible: root.linkCode !== ""
        text: root.linkCode
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.displayLarge
        font.bold: true
        font.letterSpacing: 4
      }
      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "code"
        text: "En el teléfono: WhatsApp > Dispositivos vinculados > Vincular "
          + "un dispositivo > Vincular con número de teléfono. Ingresá este código."
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "linked"
        text: "Vinculado. Confirmando el webhook…"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }
      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "done"
        text: "Cuenta vinculada y lista."
        color: Color.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "error" || root.linkStage === "no_terminal"
        text: "⚠ " + root.linkError
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
      Button {
        text: "Volver a cuentas"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        visible: root.linkStage === "done" || root.linkStage === "error"
                 || root.linkStage === "no_terminal"
        onClicked: {
          root.mode = "list"
          root.refreshAccounts()
        }
      }
    }
    }
  }

  // -------------------------------------------------------------------
  // Unlinking — typed confirmation, with a discard count read fresh at
  // the moment of asking rather than cached from when the screen opened.
  // -------------------------------------------------------------------

  property string unlinkAccountId: ""
  property string unlinkTyped: ""
  // null while the count is loading, -1 if it could not be fetched at
  // all, otherwise the real pending count.
  property var unlinkPending: null
  property bool unlinkBusy: false
  property bool unlinkDone: false
  // A non-fatal problem worth carrying to the result screen: currently the
  // wuzapi user that could not be removed. Empty means the unlink left
  // nothing outstanding.
  property string unlinkWarning: ""
  property bool unlinkTerminalSeen: false
  property string unlinkError: ""
  // Same roles as their link* counterparts.
  property bool unlinkRunning: false
  property bool unlinkResultShown: false
  property bool unlinkOwnsLog: false
  property bool unlinkRecovering: false
  // Picked up from unlink.jsonl by a new window: the discard count asked
  // before it started is gone, so it is not shown.
  property bool unlinkRecovered: false

  function unlinkConfirmed() {
    return root.unlinkTyped.trim() === root.unlinkAccountId && root.unlinkAccountId !== ""
  }

  // A real, known, non-negative count — never null (still loading) and
  // never -1 (could not be fetched). The whole point of asking at
  // confirmation time is that the number is what makes the confirmation
  // meaningful; a failed fetch must block the button exactly as hard as
  // a still-loading one, not let the unlink run with nobody knowing how
  // many conversations it is about to discard.
  function unlinkCountKnown() {
    return root.unlinkPending !== null && root.unlinkPending >= 0
  }

  function openUnlink(accountId) {
    root.mode = "unlink"
    if (root.unlinkRunning)
      return
    root.unlinkRecovered = false
    root.unlinkAccountId = accountId
    root.unlinkTyped = ""
    root.unlinkPending = null
    root.unlinkBusy = false
    root.unlinkDone = false
    root.unlinkError = ""
    root.unlinkWarning = ""
    root.fetchUnlinkCount()
  }

  // Called exactly when the confirmation is about to be asked — never
  // cached from an earlier open of this screen, because a new message can
  // arrive for this account at any time while the person is looking at it.
  function fetchUnlinkCount() {
    if (countProc.running)
      return
    root.unlinkPending = null
    countProc.command = [root.pluginDir + "bin/wa-unlink", "--json",
                          "--count-only", root.unlinkAccountId]
    countProc.running = true
  }

  function handleCountLine(line) {
    var obj = null
    try { obj = JSON.parse(String(line).trim()) } catch (e) { return }
    if (!obj || !obj.event)
      return
    if (obj.event === "count")
      root.unlinkPending = obj.pending
    else if (obj.event === "error") {
      root.unlinkPending = -1
      root.unlinkError = obj.reason || "no se pudo calcular cuántas conversaciones se perderían"
    }
  }

  Process {
    id: countProc
    stdout: SplitParser { onRead: function (line) { root.handleCountLine(line) } }
    onExited: function (exitCode) {
      if (root.unlinkPending === null) {
        root.unlinkPending = -1
        root.unlinkError = "wa-unlink --count-only terminó (código "
          + exitCode + ") sin responder."
      }
    }
  }

  function doUnlink() {
    if (root.unlinkRunning || root.unlinkRecovering || unlinkLaunch.running
        || !root.unlinkConfirmed() || !root.unlinkCountKnown())
      return
    root.unlinkBusy = true
    root.unlinkError = ""
    root.unlinkTerminalSeen = false
    root.unlinkResultShown = false
    unlinkLaunch.command = root.detached("omarchy-wa-unlink", root.unlinkLog, [root.pluginDir + "bin/wa-unlink", "--json", root.unlinkAccountId])
    unlinkLaunch.running = true
  }

  // Rebuild the whole unlink state from unlink.jsonl, from the top.
  function replayUnlink(lines) {
    root.unlinkWarning = ""
    root.unlinkError = ""
    root.unlinkDone = false
    root.unlinkTerminalSeen = false
    root.unlinkBusy = true
    for (var i = 0; i < lines.length; i++)
      root.handleUnlinkLine(lines[i])
  }

  function unlinkEnded() {
    root.unlinkRunning = false
    root.unlinkBusy = false
    if (root.visible) root.unlinkResultShown = true
  }

  function handleUnlinkLine(line) {
    var obj = null
    try { obj = JSON.parse(String(line).trim()) } catch (e) { return }
    if (!obj || !obj.event)
      return
    if (obj.event === "validating" && obj.account) {
      root.unlinkAccountId = obj.account
    } else if (obj.event === "user_removed" && obj.ok === false) {
      // Not fatal — the local cleanup goes on, and it should: an
      // unreachable wuzapi must never leave an account un-removable. But
      // a wuzapi user left behind is exactly what breaks the NEXT link of
      // this same id (wa-pair's create answers "already exists", wuzapi
      // keeps the old token, and every later call fails), so it cannot be
      // dropped on the floor the way an unconfirmed logout can.
      root.unlinkWarning = "El usuario de wuzapi"
        + (obj.user ? (" «" + obj.user + "»") : "")
        + " no se pudo borrar: " + (obj.reason || "motivo desconocido")
        + ". Si volvés a vincular esta misma cuenta va a chocar con él: "
        + "borralo a mano antes de reintentar."
    } else if (obj.event === "done") {
      root.unlinkTerminalSeen = true
      root.unlinkBusy = false
      root.unlinkDone = true
      root.refreshAccounts()
    } else if (obj.event === "error") {
      root.unlinkTerminalSeen = true
      root.unlinkBusy = false
      root.unlinkError = obj.reason || "error desconocido"
    }
  }

  Process {
    id: unlinkLaunch
    stderr: StdioCollector { id: unlinkLaunchErr }
    onExited: function (exitCode) {
      if (exitCode === 0) {
        root.unlinkOwnsLog = true
        root.unlinkRunning = true
        return
      }
      root.unlinkTerminalSeen = true
      root.unlinkError = "No se pudo iniciar wa-unlink (systemd-run, código " + exitCode
        + "). ¿Ya hay una desvinculación en curso? " + String(unlinkLaunchErr.text || "").trim()
      root.unlinkEnded()
    }
  }

  Timer {
    id: unlinkPoll
    interval: 1000; repeat: true
    running: root.unlinkRunning
    onTriggered: if (!unlinkProbe.running) unlinkProbe.running = true
  }

  Process {
    id: unlinkProbe
    command: root.probeCommand("omarchy-wa-unlink.service", root.unlinkLog)
    stdout: StdioCollector { onStreamFinished: root.onUnlinkProbe(text) }
  }

  function onUnlinkProbe(text) {
    var lines = String(text || "").split("\n")
    var active = lines.shift() === "active"
    if (root.unlinkRecovering) {
      root.unlinkRecovering = false
      if (!active && lines.join("").trim() === "")
        return
      root.unlinkOwnsLog = true
      root.unlinkRecovered = true
      root.unlinkResultShown = false
      root.mode = "unlink"
    } else if (!root.unlinkRunning) {
      return
    }
    root.replayUnlink(lines)
    if (root.unlinkRecovered) root.unlinkTyped = root.unlinkAccountId
    if (!root.unlinkTerminalSeen && active) {
      root.unlinkRunning = true
      return
    }
    if (!root.unlinkTerminalSeen) {
      root.unlinkError = "wa-unlink terminó sin "
        + "confirmar el resultado. Revisá a mano si la cuenta sigue en "
        + "accounts.json antes de reintentar — no se sabe si terminó de "
        + "desvincularse."
    }
    root.unlinkEnded()
  }

  // Wrapped for the same reason as the link-progress view: unlinkWarning
  // carries wa-unlink's own free-text reason (e.g. why the wuzapi user
  // couldn't be removed), which is not bounded, and it can appear stacked
  // under the discard count and the confirmation form.
  ScrollView {
    id: unlinkScroll
    visible: root.mode === "unlink"
    Layout.fillWidth: true; Layout.fillHeight: true
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AsNeeded

  ColumnLayout {
    width: unlinkScroll.availableWidth
    spacing: Style.spacing.lg

    Text {
      textFormat: Text.PlainText
      text: "Desvincular " + root.unlinkAccountId
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.subtitle
      font.bold: true
    }

    Text {
      textFormat: Text.PlainText
      visible: !root.unlinkRecovered
      text: root.unlinkPending === null
        ? "Calculando cuántas conversaciones pendientes se van a descartar…"
        : root.unlinkPending < 0
          ? ("⚠ No se pudo calcular: " + root.unlinkError)
          : root.unlinkPending === 0
            ? "No hay conversaciones pendientes de esta cuenta."
            : ("Se van a descartar " + root.unlinkPending + " conversación(es) "
               + "pendiente(s) de esta cuenta. No van a poder responderse "
               + "después de esto.")
      color: (root.unlinkPending !== null && root.unlinkPending !== 0) ? root.urgent : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      wrapMode: Text.Wrap
      Layout.fillWidth: true
    }

    ColumnLayout {
      visible: !root.unlinkDone
      spacing: Style.spacing.sm
      Text {
        textFormat: Text.PlainText
        text: "Escribí el id de la cuenta (" + root.unlinkAccountId + ") para confirmar:"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
      TextField {
        id: unlinkField
        Layout.fillWidth: true
        foreground: root.foreground
        enabled: !root.unlinkBusy
        text: root.unlinkTyped
        onTextChanged: root.unlinkTyped = text
      }
      RowLayout {
        spacing: Style.spacing.md
        Button {
          text: root.unlinkBusy ? "Desvinculando…" : "Desvincular"
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          // Refuses to run without a known, non-negative count: a failed
          // or still-loading fetch must block this exactly as hard as
          // each other, never let the unlink go through with nobody
          // knowing how many conversations it is about to discard.
          enabled: root.unlinkConfirmed() && !root.unlinkBusy && root.unlinkCountKnown()
          onClicked: root.doUnlink()
        }
        Button {
          // The only way out of a failed count fetch: retry it. Without
          // this the button above would just stay dead with no
          // explanation of what to do next.
          text: "Reintentar cálculo"
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          visible: root.unlinkPending !== null && root.unlinkPending < 0 && !root.unlinkBusy
          onClicked: root.fetchUnlinkCount()
        }
        Button {
          text: "Cancelar"
          foreground: root.foreground
          fontFamily: root.fontFamily
          bordered: true
          enabled: !root.unlinkBusy
          onClicked: root.mode = "list"
        }
      }
      Text {
        textFormat: Text.PlainText
        visible: root.unlinkError !== "" && !root.unlinkBusy
        text: "⚠ " + root.unlinkError
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
    }

    ColumnLayout {
      visible: root.unlinkDone
      spacing: Style.spacing.sm
      // Same rule as the linking screen: the bold, green, unqualified
      // "done" line is only shown when nothing is left outstanding.
      Text {
        textFormat: Text.PlainText
        visible: root.unlinkWarning === ""
        text: "Cuenta desvinculada."
        color: Color.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Text {
        textFormat: Text.PlainText
        visible: root.unlinkWarning !== ""
        text: "La cuenta se borró de este equipo."
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
      Text {
        textFormat: Text.PlainText
        visible: root.unlinkWarning !== ""
        text: "⚠ " + root.unlinkWarning
        color: root.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
      Button {
        text: "Volver a cuentas"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        onClicked: root.mode = "list"
      }
    }
  }
  }

  // -------------------------------------------------------------------
  // Tone editing — a hash round-trip so a hand edit to the file since it
  // was read refuses the save instead of clobbering it.
  // -------------------------------------------------------------------

  property string toneAccountId: ""
  property string toneHash: ""
  property string toneStatus: ""
  property bool toneStatusWarn: false
  property bool toneBusy: false
  // True only between a successful read and leaving the editor. Guardar is
  // gated on it, so there is no window where the button is live over text
  // that is not this account's — see openTone().
  property bool toneLoaded: false
  // The text as it is on disk, as of the last successful read or save.
  // What "unsaved edits" means, and the only thing Recargar can throw away.
  property string toneDiskText: ""
  property bool toneReloadArmed: false

  function toneDirty() {
    return root.toneLoaded && toneBox.text !== root.toneDiskText
  }

  function openTone(accountId) {
    root.mode = "tone"
    root.toneAccountId = accountId
    // Clearing the box is the whole point: it used to keep the PREVIOUS
    // account's prose on screen under the new account's heading until the
    // fetch returned, and Guardar was live over it the whole time. Nothing
    // could be lost (fetchTone below clears the hash, and wa-reply always
    // refuses an empty one) but the person was reading one account's prompt
    // believing it was another's, which is worse than a blank box.
    toneBox.text = ""
    root.fetchTone()
  }

  function fetchTone() {
    if (toneGetProc.running)
      return
    root.toneLoaded = false
    root.toneHash = ""
    root.toneDiskText = ""
    root.toneReloadArmed = false
    root.toneStatus = "Cargando…"
    root.toneStatusWarn = false
    toneGetProc.command = [root.pluginDir + "bin/wa-reply", "tone-get", root.toneAccountId]
    toneGetProc.running = true
  }

  // Recargar throws away whatever is in the box and re-reads the file, so
  // with unsaved edits it needs a deliberate second press rather than a
  // single mis-click. Armed state is dropped the moment the reload happens,
  // the editor is left, or another account is opened.
  function requestToneReload() {
    if (root.toneDirty() && !root.toneReloadArmed) {
      root.toneReloadArmed = true
      return
    }
    root.fetchTone()
  }

  function saveTone(text) {
    if (toneSetProc.running)
      return
    root.toneBusy = true
    root.toneStatus = ""
    toneSetProc.command = [root.pluginDir + "bin/wa-reply", "tone-set",
                            root.toneAccountId, "--text", text, "--hash", root.toneHash]
    toneSetProc.running = true
  }

  Process {
    id: toneGetProc
    stdout: StdioCollector {
      onStreamFinished: {
        var r = null
        try { r = JSON.parse(text) } catch (e) { r = null }
        if (r && r.ok) {
          toneBox.text = r.text
          root.toneDiskText = r.text
          root.toneHash = r.hash
          root.toneLoaded = true
          root.toneStatus = ""
          root.toneStatusWarn = false
        } else {
          // A failed read leaves toneLoaded false, so Guardar stays dead:
          // there is no hash to save against and no text worth saving.
          root.toneStatus = (r && r.error) ? r.error : "no se pudo leer el tono"
          root.toneStatusWarn = true
        }
      }
    }
  }

  Process {
    id: toneSetProc
    stdout: StdioCollector {
      onStreamFinished: {
        root.toneBusy = false
        var r = null
        try { r = JSON.parse(text) } catch (e) { r = null }
        if (r && r.ok) {
          // The hash the save just wrote — carried forward so the NEXT
          // save's conflict check compares against what is on disk now.
          root.toneHash = r.hash
          root.toneDiskText = toneBox.text
          root.toneStatus = "guardado"
          root.toneStatusWarn = false
        } else {
          // On a conflict, this is exactly the message wa-reply returns —
          // it explains that nothing was written and what to do. Shown
          // verbatim, never merged automatically: the person reloads (see
          // "Recargar") and reapplies their edit by hand.
          root.toneStatus = (r && r.error) ? r.error : "no se pudo guardar"
          root.toneStatusWarn = true
        }
      }
    }
  }

  ColumnLayout {
    visible: root.mode === "tone"
    Layout.fillWidth: true; Layout.fillHeight: true
    spacing: Style.spacing.md

    Text {
      textFormat: Text.PlainText
      text: "Tono — " + root.toneAccountId
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.subtitle
      font.bold: true
    }

    // The tone box holds a whole prompt file — it will certainly overflow
    // a fixed-height panel. Same ScrollView-around-TextArea idiom as the
    // composer in ChatWindow.qml: `toneBox` stays the TextArea's own id, so
    // every toneBox.text reference (toneDirty(), saveTone(), the fetch
    // handlers) and its own enabled/selectByMouse keep working unchanged.
    ScrollView {
      Layout.fillWidth: true; Layout.fillHeight: true
      clip: true
      ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
      ScrollBar.vertical.policy: ScrollBar.AsNeeded

      TextArea {
        id: toneBox
        wrapMode: TextEdit.Wrap
        color: root.foreground
        selectByMouse: true
        enabled: !root.toneBusy
        background: Rectangle {
          color: Style.normalFillFor(root.foreground, Color.accent)
          radius: Style.cornerRadius
        }
      }
    }

    Text {
      textFormat: Text.PlainText
      visible: root.toneStatus !== ""
      text: root.toneStatus
      color: root.toneStatusWarn ? root.urgent : Color.accent
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.Wrap
      Layout.fillWidth: true
    }

    RowLayout {
      spacing: Style.spacing.md
      Button {
        text: root.toneBusy ? "Guardando…" : "Guardar"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        // Dead until this account's own text has actually arrived.
        enabled: !root.toneBusy && root.toneLoaded
        onClicked: root.saveTone(toneBox.text)
      }
      Button {
        text: root.toneReloadArmed ? "¿Descartar cambios?" : "Recargar"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.toneBusy
        onClicked: root.requestToneReload()
      }
      Button {
        text: "Volver"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.toneBusy
        onClicked: {
          root.toneReloadArmed = false
          root.mode = "list"
        }
      }
    }
  }

  // -------------------------------------------------------------------
  // Quick-reply editing — same hash round-trip, but the hash is of the
  // WHOLE accounts.json, so a hand edit to a DIFFERENT account can also
  // block this save. Said plainly in the view below.
  // -------------------------------------------------------------------

  property string quickAccountId: ""
  property var quickList: []
  property string quickHash: ""
  property string quickNewText: ""
  property string quickStatus: ""
  property bool quickStatusWarn: false
  property bool quickBusy: false

  function openQuick(accountId) {
    root.mode = "quick"
    root.quickAccountId = accountId
    root.quickList = []
    root.quickHash = ""
    root.quickNewText = ""
    root.quickStatus = "Cargando…"
    root.quickStatusWarn = false
    root.fetchQuick()
  }

  function fetchQuick() {
    if (quickGetProc.running)
      return
    quickGetProc.command = [root.pluginDir + "bin/wa-reply", "quick-get", root.quickAccountId]
    quickGetProc.running = true
  }

  function quickAdd() {
    var t = root.quickNewText.trim()
    if (t === "")
      return
    var list = root.quickList.slice()
    list.push(t)
    root.quickList = list
    root.quickNewText = ""
  }

  function quickRemove(index) {
    var list = root.quickList.slice()
    list.splice(index, 1)
    root.quickList = list
  }

  // Mutates the array IN PLACE rather than reassigning root.quickList:
  // reassigning would reset the ListView's model on every keystroke
  // (dropping focus mid-edit), and saveQuick() reads root.quickList
  // directly at save time regardless — it never needs a change
  // notification to fire. This also means the field's every keystroke
  // reaches the model immediately, not just on focus loss: relying on
  // onEditingFinished to flush before a same-tick Guardar click would
  // silently drop the last edit if focus never actually left the field
  // (Button here does not steal focus on click).
  function quickUpdate(index, text) {
    if (index >= 0 && index < root.quickList.length)
      root.quickList[index] = text
  }

  function saveQuick() {
    if (quickSetProc.running)
      return
    root.quickBusy = true
    root.quickStatus = ""
    quickSetProc.command = [root.pluginDir + "bin/wa-reply", "quick-set",
                             root.quickAccountId, "--replies",
                             JSON.stringify(root.quickList), "--hash", root.quickHash]
    quickSetProc.running = true
  }

  Process {
    id: quickGetProc
    stdout: StdioCollector {
      onStreamFinished: {
        var r = null
        try { r = JSON.parse(text) } catch (e) { r = null }
        if (r && r.ok) {
          root.quickList = Array.isArray(r.quick_replies) ? r.quick_replies.slice() : []
          root.quickHash = r.hash
          root.quickStatus = ""
          root.quickStatusWarn = false
        } else {
          root.quickStatus = (r && r.error) ? r.error : "no se pudo leer la lista"
          root.quickStatusWarn = true
        }
      }
    }
  }

  Process {
    id: quickSetProc
    stdout: StdioCollector {
      onStreamFinished: {
        root.quickBusy = false
        var r = null
        try { r = JSON.parse(text) } catch (e) { r = null }
        if (r && r.ok) {
          root.quickList = Array.isArray(r.quick_replies) ? r.quick_replies.slice() : root.quickList
          root.quickHash = r.hash
          root.quickStatus = "guardado"
          root.quickStatusWarn = false
        } else {
          // Same stance as tone-set: show wa-reply's own message (invalid
          // list, or a conflict naming exactly what to do), never merge.
          root.quickStatus = (r && r.error) ? r.error : "no se pudo guardar"
          root.quickStatusWarn = true
        }
      }
    }
  }

  ColumnLayout {
    visible: root.mode === "quick"
    Layout.fillWidth: true; Layout.fillHeight: true
    spacing: Style.spacing.md

    Text {
      textFormat: Text.PlainText
      text: "Respuestas rápidas — " + root.quickAccountId
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.subtitle
      font.bold: true
    }

    Text {
      textFormat: Text.PlainText
      text: "Se guarda la lista completa de una vez, y se compara todo "
        + "accounts.json: si alguien edita a mano OTRA cuenta mientras "
        + "tanto, este guardado también se rechaza."
      color: Color.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.Wrap
      Layout.fillWidth: true
    }

    ListView {
      Layout.fillWidth: true; Layout.fillHeight: true
      model: root.quickList
      spacing: Style.spacing.xs
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
      delegate: Rectangle {
        id: qRow
        required property int index
        required property string modelData
        width: ListView.view.width
        height: Math.max(qField.implicitHeight, qDel.implicitHeight) + Style.spacing.sm
        color: "transparent"
        RowLayout {
          anchors.fill: parent
          spacing: Style.spacing.sm
          TextField {
            id: qField
            Layout.fillWidth: true
            foreground: root.foreground
            text: qRow.modelData
            onTextChanged: root.quickUpdate(qRow.index, text)
          }
          Button {
            id: qDel
            text: "Eliminar"
            foreground: root.foreground
            fontFamily: root.fontFamily
            bordered: true
            onClicked: root.quickRemove(qRow.index)
          }
        }
      }
    }

    RowLayout {
      spacing: Style.spacing.sm
      TextField {
        id: quickNewField
        Layout.fillWidth: true
        foreground: root.foreground
        placeholderText: "Nueva respuesta rápida…"
        text: root.quickNewText
        onTextChanged: root.quickNewText = text
        Keys.onReturnPressed: root.quickAdd()
        Keys.onEnterPressed: root.quickAdd()
      }
      Button {
        text: "Agregar"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: root.quickNewText.trim() !== ""
        onClicked: root.quickAdd()
      }
    }

    Text {
      textFormat: Text.PlainText
      visible: root.quickStatus !== ""
      text: root.quickStatus
      color: root.quickStatusWarn ? root.urgent : Color.accent
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.Wrap
      Layout.fillWidth: true
    }

    RowLayout {
      spacing: Style.spacing.md
      Button {
        text: root.quickBusy ? "Guardando…" : "Guardar"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.quickBusy
        onClicked: root.saveQuick()
      }
      Button {
        text: "Recargar"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.quickBusy
        onClicked: root.fetchQuick()
      }
      Button {
        text: "Volver"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.quickBusy
        onClicked: root.mode = "list"
      }
    }
  }
}
