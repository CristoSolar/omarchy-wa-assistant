// Panel.qml
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

PopupWindow {
    id: panel
    implicitWidth: 460
    implicitHeight: 420
    visible: false
    color: "#1e2127"

    property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
    property var entries: []
    property var current: null
    property string draft: ""
    property string status: ""
    // The status line turns to a warning color for the one outcome that is
    // neither a clean success nor a clean failure: a send whose result is
    // unknown (see onClicked below).
    property bool statusWarn: false
    // A send that timed out mid-flight may already have reached the
    // recipient. Blocking the button until the entry is reopened stops the
    // obvious reflex of hitting "Enviar" again, which would risk a real
    // double-send.
    property bool sendBlocked: false

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

    function run(args, done) {
        proc.command = [pluginDir + "bin/wa-reply"].concat(args)
        proc.done = done
        proc.running = true
    }

    Process {
        id: proc
        property var done: null
        stdout: StdioCollector {
            onStreamFinished: if (proc.done) proc.done(text)
        }
    }

    Component.onCompleted: refresh()

    function refresh() {
        run(["list"], function (out) {
            try { panel.entries = JSON.parse(out) } catch (e) { panel.entries = [] }
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
                width: ListView.view.width; height: 48
                color: ma.containsMouse ? "#2c313a" : "transparent"
                radius: 4
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 6; spacing: 2
                    Text {
                        text: modelData.push_name +
                              (modelData.count > 1 ? "  (" + modelData.count + ")" : "")
                        color: "#e6e6e6"; font.pixelSize: 13
                    }
                    Text {
                        text: modelData.text
                        color: "#9aa0a6"; font.pixelSize: 11
                        elide: Text.ElideRight; Layout.fillWidth: true
                    }
                }
                MouseArea {
                    id: ma; anchors.fill: parent; hoverEnabled: true
                    onClicked: {
                        panel.current = modelData
                        panel.draft = ""
                        panel.status = ""
                        panel.statusWarn = false
                        panel.sendBlocked = false
                    }
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

            TextField {
                id: idea
                Layout.fillWidth: true
                placeholderText: "Qué querés responder…"
                color: "#e6e6e6"
                background: Rectangle { color: "#2c313a"; radius: 4 }
                onAccepted: {
                    panel.status = "redactando…"
                    panel.statusWarn = false
                    panel.run(["draft", panel.current.id, "--idea", text], function (out) {
                        try {
                            const d = JSON.parse(out)
                            panel.draft = d.draft
                            panel.status = d.error ? ("error: " + d.error) : ""
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
                    enabled: draftBox.text.trim().length > 0 && !panel.sendBlocked
                    onClicked: {
                        panel.status = "enviando…"
                        panel.statusWarn = false
                        panel.run(["send", panel.current.id, "--text", draftBox.text],
                                  function (out) {
                            try {
                                const r = JSON.parse(out)
                                if (r.ok) {
                                    panel.status = r.dry_run ? "DRY RUN — no se envió" : "enviado"
                                    panel.statusWarn = false
                                    panel.current = null
                                    panel.refresh()
                                } else if (r.unknown) {
                                    // The request timed out: the message may
                                    // or may not have gone out. Never treat
                                    // this as a plain failure — the obvious
                                    // retry would risk sending the same
                                    // message twice to a real person. Keep
                                    // the entry visible (it stays in the
                                    // queue on the wa-reply side too) and
                                    // block this button until the entry is
                                    // reopened.
                                    panel.status = "sin confirmación: puede que el mensaje ya se haya enviado — revisá WhatsApp antes de reintentar"
                                    panel.statusWarn = true
                                    panel.sendBlocked = true
                                    panel.refresh()
                                } else {
                                    panel.status = "falló: " + r.error
                                    panel.statusWarn = true
                                }
                            } catch (e) { panel.status = "falló"; panel.statusWarn = true }
                        })
                    }
                }
                Button { text: "Volver"; onClicked: panel.current = null }
            }
        }
    }
}
