// SettingsView.qml
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell.Io
import qs.Ui
import qs.Commons

// The settings screen: list accounts, link a new one, unlink one behind a
// typed confirmation, and edit each account's tone and quick replies.
//
// Embedded as a plain child of Panel.qml (not a Loader), so it is never
// destroyed while a link or unlink is mid-flight — toggling the gear only
// changes `visible`, the same technique the queue/detail split in Panel.qml
// already uses. This is deliberate: a pairing run takes minutes, and the
// person may close the panel to do something else while it waits on the
// history dump. The Process instances below keep running regardless.
//
// This view owns its OWN Processes for everything it does. It never calls
// into Panel.qml's `run()`, `doSend()` or any of its Process ids — that is
// the boundary that makes "the settings screen must not be able to send a
// message" true by construction rather than by discipline: there is no
// send path here at all, only wa-pair/wa-unlink/wa-reply's read/write
// subcommands for tone and quick replies.
ColumnLayout {
  id: root
  spacing: Style.spacing.lg

  property string pluginDir: ""
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

  // Called by Panel.qml whenever the gear opens this screen. Deliberately
  // does NOT reset `mode` — a link or unlink in progress must keep showing
  // its own progress when the person reopens settings, not snap back to
  // the account list and lose their place.
  function onShown() {
    if (root.mode === "list")
      root.refreshAccounts()
  }

  Component.onCompleted: root.refreshAccounts()

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

    ListView {
      Layout.fillWidth: true; Layout.fillHeight: true
      model: root.accounts
      spacing: Style.spacing.sm
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
  // line over several minutes; this reads it incrementally via SplitParser
  // (onRead fires per line, as it arrives) rather than collecting the
  // whole stream and waiting for exit, which is what makes the code and
  // the history-wait state show up while they are still happening instead
  // of only once the process is long gone.
  // -------------------------------------------------------------------

  property string linkAccountId: ""
  property string linkNumber: ""
  // form | validating | code | linked | history_waiting | history_purged
  // | done | error | no_terminal
  property string linkStage: "form"
  property string linkCode: ""
  property string linkError: ""
  property string linkPurgeMessage: ""
  property bool linkPurgeWarn: false
  // Set the moment a "done" or "error" event is actually parsed. Checked
  // in pairProc's onExited: if the process ends and this is still false,
  // the stream ended with no terminal event — the script's own header
  // says a SIGKILL cannot run its EXIT trap, so this is a real outcome
  // the UI must show plainly, not a bug to paper over with a spinner.
  property bool linkTerminalSeen: false

  function openLink() {
    root.mode = "link"
    root.linkStage = "form"
    root.linkAccountId = ""
    root.linkNumber = ""
    root.linkCode = ""
    root.linkError = ""
    root.linkPurgeMessage = ""
    root.linkPurgeWarn = false
  }

  function startLink() {
    if (pairProc.running)
      return
    var id = root.linkAccountId.trim()
    var num = root.linkNumber.trim()
    if (id === "" || num === "")
      return
    root.linkStage = "validating"
    root.linkCode = ""
    root.linkError = ""
    root.linkPurgeMessage = ""
    root.linkPurgeWarn = false
    root.linkTerminalSeen = false
    pairProc.command = [root.pluginDir + "bin/wa-pair", id, num, "--json"]
    pairProc.running = true
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
      break
    case "code":
      root.linkCode = obj.code || ""
      root.linkStage = "code"
      break
    case "linked":
      root.linkStage = "linked"
      break
    case "history_waiting":
      root.linkStage = "history_waiting"
      break
    case "history_purged":
      // Never claim success from `seen` alone. `verified` false means
      // either nothing was ever seen (we cannot tell "no history" from
      // "the dump had not arrived yet") or rows remained after the purge
      // — both are honestly reported, neither is treated as done.
      root.linkStage = "history_purged"
      if (obj.seen === false) {
        root.linkPurgeMessage = "No se vio ninguna fila de historial para "
          + "esta cuenta. No se puede saber si no había historial o si "
          + "WhatsApp todavía no había empezado a mandarlo. Nada se dio "
          + "por purgado."
        root.linkPurgeWarn = true
      } else if (obj.verified === true) {
        root.linkPurgeMessage = "Historial purgado: "
          + (obj.rows !== undefined ? obj.rows : "?")
          + " fila(s) vistas, confirmado que la tabla quedó vacía."
        root.linkPurgeWarn = false
      } else {
        root.linkPurgeMessage = "El historial puede NO haberse borrado del "
          + "todo. Filas vistas: " + (obj.rows !== undefined ? obj.rows : "?")
          + (obj.remaining !== undefined
             ? (", filas restantes: " + obj.remaining) : "") + "."
        root.linkPurgeWarn = true
      }
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

  Process {
    id: pairProc
    stdout: SplitParser { onRead: function (line) { root.handlePairLine(line) } }
    onExited: function (exitCode) {
      if (!root.linkTerminalSeen) {
        root.linkStage = "no_terminal"
        root.linkError = "wa-pair terminó (código " + exitCode + ") sin "
          + "confirmar el resultado. Puede haber sido interrumpido a la "
          + "fuerza. No se sabe si la cuenta quedó vinculada — revisá "
          + "manualmente antes de reintentar (por ejemplo, mirando si la "
          + "cuenta aparece en la lista, o los logs del contenedor)."
      }
    }
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

    ColumnLayout {
      visible: root.linkStage !== "form"
      Layout.fillWidth: true
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
      // The minutes-long stretch. Named plainly so nobody mistakes it for
      // a hang — see the module header for why this exact wording exists.
      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "history_waiting"
        text: "Esperando que WhatsApp termine de mandar el historial. Esto "
          + "puede tardar varios minutos — es normal, no cierres esta pantalla."
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        wrapMode: Text.Wrap
        Layout.fillWidth: true
      }
      Text {
        textFormat: Text.PlainText
        visible: root.linkStage === "history_purged" || root.linkStage === "done"
        text: root.linkPurgeMessage
        color: root.linkPurgeWarn ? root.urgent : Color.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
        Layout.fillWidth: true
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
  property bool unlinkTerminalSeen: false
  property string unlinkError: ""

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
    root.unlinkAccountId = accountId
    root.unlinkTyped = ""
    root.unlinkPending = null
    root.unlinkBusy = false
    root.unlinkDone = false
    root.unlinkError = ""
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
    if (unlinkProc.running || !root.unlinkConfirmed() || !root.unlinkCountKnown())
      return
    root.unlinkBusy = true
    root.unlinkError = ""
    root.unlinkTerminalSeen = false
    unlinkProc.command = [root.pluginDir + "bin/wa-unlink", "--json", root.unlinkAccountId]
    unlinkProc.running = true
  }

  function handleUnlinkLine(line) {
    var obj = null
    try { obj = JSON.parse(String(line).trim()) } catch (e) { return }
    if (!obj || !obj.event)
      return
    if (obj.event === "done") {
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
    id: unlinkProc
    stdout: SplitParser { onRead: function (line) { root.handleUnlinkLine(line) } }
    onExited: function (exitCode) {
      root.unlinkBusy = false
      if (!root.unlinkTerminalSeen) {
        root.unlinkError = "wa-unlink terminó (código " + exitCode + ") sin "
          + "confirmar el resultado. Revisá a mano si la cuenta sigue en "
          + "accounts.json antes de reintentar — no se sabe si terminó de "
          + "desvincularse."
      }
    }
  }

  ColumnLayout {
    visible: root.mode === "unlink"
    Layout.fillWidth: true; Layout.fillHeight: true
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
      Text {
        textFormat: Text.PlainText
        text: "Cuenta desvinculada."
        color: Color.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
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

  // -------------------------------------------------------------------
  // Tone editing — a hash round-trip so a hand edit to the file since it
  // was read refuses the save instead of clobbering it.
  // -------------------------------------------------------------------

  property string toneAccountId: ""
  property string toneHash: ""
  property string toneStatus: ""
  property bool toneStatusWarn: false
  property bool toneBusy: false

  function openTone(accountId) {
    root.mode = "tone"
    root.toneAccountId = accountId
    root.toneHash = ""
    root.toneStatus = "Cargando…"
    root.toneStatusWarn = false
    root.fetchTone()
  }

  function fetchTone() {
    if (toneGetProc.running)
      return
    toneGetProc.command = [root.pluginDir + "bin/wa-reply", "tone-get", root.toneAccountId]
    toneGetProc.running = true
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
          root.toneHash = r.hash
          root.toneStatus = ""
          root.toneStatusWarn = false
        } else {
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

    TextArea {
      id: toneBox
      Layout.fillWidth: true; Layout.fillHeight: true
      wrapMode: TextEdit.Wrap
      color: root.foreground
      selectByMouse: true
      enabled: !root.toneBusy
      background: Rectangle {
        color: Style.normalFillFor(root.foreground, Color.accent)
        radius: Style.cornerRadius
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
        enabled: !root.toneBusy
        onClicked: root.saveTone(toneBox.text)
      }
      Button {
        text: "Recargar"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.toneBusy
        onClicked: root.fetchTone()
      }
      Button {
        text: "Volver"
        foreground: root.foreground
        fontFamily: root.fontFamily
        bordered: true
        enabled: !root.toneBusy
        onClicked: root.mode = "list"
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
