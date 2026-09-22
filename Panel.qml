// Panel.qml
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

PopupWindow {
    id: panel
    implicitWidth: 460
    implicitHeight: 460
    visible: false
    color: "#1e2127"

    property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
    property var entries: []
    // The selected entry, and its id kept separately so refresh() can
    // re-resolve the entry object from the freshly loaded queue. Anything
    // the UI decides about an entry must come from what is on disk, not
    // from a snapshot taken when it was clicked.
    property var current: null
    property string currentId: ""
    property string draft: ""
    property string status: ""
    // The status line turns to a warning color for any outcome that is
    // neither a clean success nor a clean failure.
    property bool statusWarn: false
    // Derived from the DURABLE status in queue.json, never from a flag we
    // set ourselves: an entry left "unknown" by an earlier send may already
    // have reached the recipient. A bar restart, a Loader reload or a
    // refresh cannot clear this, because it is recomputed from disk every
    // time. `wa-reply send` refuses such an entry too, so the two halves of
    // the defence agree.
    readonly property bool needsAck: panel.current !== null
                                     && (panel.current.status === "unknown"
                                         || panel.current.status === "sending")
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

    // BarWidget hands this in after loading the popup: PopupWindow has no
    // default position without an anchor.item/window to position against.
    property Item anchorItem: null
    anchor.item: panel.anchorItem

    // The popup owns its own visibility instead of a hardcoded
    // `visible: true`, so BarWidget's Loader can stay active permanently
    // (loading once) and just call these instead of tearing the window
    // down and rebuilding it on every click.
    function open() {
        panel.visible = true
        panel.refresh()
    }
    function close() {
        panel.visible = false
    }
    function toggle() {
        if (panel.visible) panel.close(); else panel.open()
    }

    // Everything except the send. Refuses to start while a send is in
    // flight: these used to share one Process with the send, so pressing
    // Enter in the idea field or toggling the popup mid-send reassigned
    // proc.command/proc.done, the send's completion handler never ran, and
    // inFlight stayed true — the send button was dead for the rest of the
    // session and the user never learned the outcome of a send that had
    // actually happened.
    function run(args, done) {
        if (panel.inFlight || proc.running)
            return false
        proc.command = [panel.pluginDir + "bin/wa-reply"].concat(args)
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

    function select(entry) {
        panel.current = entry
        panel.currentId = entry ? entry.id : ""
        panel.draft = ""
        panel.status = ""
        panel.statusWarn = false
        panel.acknowledged = false
        panel.sendBlocked = false
    }

    Component.onCompleted: refresh()

    function refresh() {
        run(["list"], function (out) {
            var list = []
            try { list = JSON.parse(out) } catch (e) { list = [] }
            panel.entries = list
            // Re-resolve the open entry against what is now on disk, so the
            // detail view reflects the stored status (and any newer message
            // in the same chat) rather than the click-time snapshot.
            if (panel.currentId !== "") {
                var found = null
                for (var i = 0; i < list.length; i++) {
                    if (list[i].id === panel.currentId) { found = list[i]; break }
                }
                panel.current = found
                if (found === null)
                    panel.currentId = ""
            }
        })
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        Text {
            text: panel.current ? panel.current.push_name : "Pendientes"
            color: "#e6e6e6"; font.pixelSize: 15; font.bold: true
        }

        ListView {
            visible: !panel.current
            Layout.fillWidth: true; Layout.fillHeight: true
            model: panel.entries
            spacing: 4
            delegate: Rectangle {
                id: row
                required property var modelData
                width: ListView.view.width; height: 52
                color: ma.containsMouse ? "#2c313a" : "transparent"
                radius: 4
                // An entry whose last send outcome is unknown must be
                // visible AS SUCH in the list, not only once it is opened.
                // "sending" is an entry whose wa-reply was killed mid-POST:
                // the same ambiguity as "unknown", so the same marker.
                border.color: (row.modelData.status === "unknown"
                               || row.modelData.status === "sending")
                              ? "#e06c75" : "transparent"
                border.width: (row.modelData.status === "unknown"
                               || row.modelData.status === "sending") ? 1 : 0
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 6; spacing: 2
                    RowLayout {
                        spacing: 6
                        Text {
                            text: row.modelData.push_name +
                                  (row.modelData.count > 1
                                   ? "  (" + row.modelData.count + ")" : "")
                            color: "#e6e6e6"; font.pixelSize: 13
                        }
                        Text {
                            visible: row.modelData.status === "unknown"
                                     || row.modelData.status === "sending"
                            text: row.modelData.status === "sending"
                                  ? "⚠ envío interrumpido" : "⚠ sin confirmar"
                            color: "#e06c75"; font.pixelSize: 11
                        }
                        Text {
                            visible: row.modelData.status === "failed"
                            text: "falló"
                            color: "#e5c07b"; font.pixelSize: 11
                        }
                    }
                    Text {
                        text: row.modelData.text
                        color: "#9aa0a6"; font.pixelSize: 11
                        elide: Text.ElideRight; Layout.fillWidth: true
                    }
                }
                MouseArea {
                    id: ma; anchors.fill: parent; hoverEnabled: true
                    onClicked: panel.select(row.modelData)
                }
            }
        }

        ColumnLayout {
            visible: panel.current !== null
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 8

            Text {
                text: panel.current ? panel.current.text : ""
                color: "#9aa0a6"; font.pixelSize: 12
                wrapMode: Text.Wrap; Layout.fillWidth: true
            }

            // The durable "unknown" warning, with the deliberate
            // acknowledgement that re-arms the send button.
            Rectangle {
                visible: panel.needsAck
                Layout.fillWidth: true
                implicitHeight: ackCol.implicitHeight + 12
                color: "#3a2a2c"; radius: 4
                border.color: "#e06c75"; border.width: 1
                ColumnLayout {
                    id: ackCol
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6
                    spacing: 6
                    Text {
                        text: (panel.current && panel.current.status === "sending"
                               ? "⚠ Un envío anterior de esta entrada quedó interrumpido "
                                 + "a mitad de camino (se cerró wa-reply antes de saber "
                                 + "el resultado): "
                               : "⚠ El envío anterior quedó sin confirmación: ")
                              + "puede que este mensaje YA se haya enviado. Revisá "
                              + "WhatsApp antes de reintentar — si lo reenviás sin "
                              + "mirar, la persona puede recibirlo dos veces."
                        color: "#e6e6e6"; font.pixelSize: 11
                        wrapMode: Text.Wrap; Layout.fillWidth: true
                    }
                    Button {
                        visible: !panel.acknowledged
                        text: "Revisé WhatsApp, quiero reenviar"
                        onClicked: panel.acknowledged = true
                    }
                    Text {
                        visible: panel.acknowledged
                        text: "Reenvío habilitado para esta entrada."
                        color: "#e5c07b"; font.pixelSize: 11
                    }
                }
            }

            TextField {
                id: idea
                Layout.fillWidth: true
                placeholderText: "Qué querés responder…"
                color: "#e6e6e6"
                background: Rectangle { color: "#2c313a"; radius: 4 }
                enabled: !panel.inFlight
                onAccepted: {
                    if (panel.inFlight)
                        return
                    panel.status = "redactando…"
                    panel.statusWarn = false
                    panel.run(["draft", panel.currentId, "--idea", idea.text],
                              function (out) {
                        try {
                            const d = JSON.parse(out)
                            panel.draft = d.draft
                            panel.status = d.error ? ("error: " + d.error) : ""
                            panel.statusWarn = d.error ? true : false
                        } catch (e) { panel.status = "no se pudo redactar" }
                    })
                }
            }

            TextArea {
                id: draftBox
                Layout.fillWidth: true; Layout.fillHeight: true
                text: panel.draft
                color: "#e6e6e6"; wrapMode: TextEdit.Wrap
                background: Rectangle { color: "#2c313a"; radius: 4 }
            }

            Text {
                text: panel.status
                color: panel.statusWarn ? "#e06c75" : "#e5c07b"
                font.pixelSize: 11
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            RowLayout {
                spacing: 8
                Button {
                    text: "Enviar"
                    enabled: draftBox.text.trim().length > 0
                             && !panel.sendBlocked
                             && !panel.inFlight
                             && (!panel.needsAck || panel.acknowledged)
                    onClicked: {
                        if (panel.inFlight || sendProc.running)
                            return
                        panel.status = "enviando…"
                        panel.statusWarn = false
                        panel.inFlight = true
                        var args = ["send", panel.currentId, "--text", draftBox.text]
                        if (panel.needsAck && panel.acknowledged)
                            args.push("--acknowledged")
                        sendProc.command = [panel.pluginDir + "bin/wa-reply"].concat(args)
                        sendProc.done = function (out) {
                            // Cleared first, before anything that can throw,
                            // so a malformed response never leaves the
                            // button permanently disabled.
                            panel.inFlight = false
                            try {
                                const r = JSON.parse(out)
                                if (r.ok && r.dry_run) {
                                    // Nothing was sent, so nothing changes:
                                    // the entry stays in the queue and stays
                                    // open. Say so plainly.
                                    panel.status = "DRY RUN — no se envió nada. "
                                                 + "La entrada sigue pendiente en la cola."
                                    panel.statusWarn = false
                                    panel.refresh()
                                } else if (r.ok && r.superseded) {
                                    panel.status = "enviado. Llegó un mensaje nuevo en "
                                                 + "este chat mientras redactabas, así que "
                                                 + "la entrada sigue en la cola."
                                    panel.statusWarn = true
                                    panel.refresh()
                                } else if (r.ok) {
                                    panel.status = "enviado"
                                    panel.statusWarn = false
                                    panel.select(null)
                                    panel.refresh()
                                } else if (r.needs_ack) {
                                    panel.status = r.error
                                    panel.statusWarn = true
                                    panel.acknowledged = false
                                    panel.refresh()
                                } else if (r.unknown) {
                                    // The request timed out: the message may
                                    // or may not have gone out. wa-reply has
                                    // recorded "unknown" on disk, so the
                                    // refresh below is what re-arms the
                                    // block — it survives a restart.
                                    panel.status = "sin confirmación: puede que el mensaje ya se haya enviado — revisá WhatsApp antes de reintentar"
                                    panel.statusWarn = true
                                    panel.acknowledged = false
                                    panel.refresh()
                                } else {
                                    panel.status = "falló: " + r.error
                                    panel.statusWarn = true
                                    panel.refresh()
                                }
                            } catch (e) {
                                // A send whose response could not be parsed
                                // (wa-reply crashed or was truncated
                                // mid-write) is exactly as ambiguous as an
                                // `unknown` outcome — it may already have
                                // reached WhatsApp — and unlike `unknown` we
                                // cannot rely on anything having been written
                                // to disk, so it also gets the volatile
                                // block. Scoped to the send path only.
                                panel.status = "sin confirmación: puede que el mensaje ya se haya enviado — revisá WhatsApp antes de reintentar"
                                panel.statusWarn = true
                                panel.sendBlocked = true
                                panel.refresh()
                            }
                        }
                        sendProc.running = true
                    }
                }
                Button {
                    text: "Volver"
                    enabled: !panel.inFlight
                    onClicked: panel.select(null)
                }
            }
        }
    }
}
