// BarWidget.qml
import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

Item {
    id: root
    property int pending: 0
    property bool bridgeOk: true
    property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")

    implicitWidth: visible ? row.implicitWidth + 16 : 0
    implicitHeight: 24
    visible: pending > 0 || !bridgeOk

    Process {
        id: status
        command: [root.pluginDir + "bin/wa-status"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const d = JSON.parse(text)
                    root.pending = d.pending
                    root.bridgeOk = d.ok && d.logged_in
                } catch (e) {
                    root.bridgeOk = false
                }
            }
        }
    }

    Timer {
        interval: 3000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: status.running = true
    }

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Text {
            text: root.bridgeOk ? "WA " + root.pending : "WA ✕"
            color: root.bridgeOk ? "#e6e6e6" : "#e06c75"
            font.pixelSize: 12
        }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: if (panelLoader.item) panelLoader.item.toggle()
    }

    // Panel.qml is a PopupWindow, not an Item. Loading a window-rooted
    // component through a Loader does work in Quickshell — the sibling
    // plugins io.github.cristosolar.queens and admin.clock on this machine
    // both do exactly this (Loader { active: true; source: "Panel.qml" }) —
    // but two things the brief's version got wrong would have broken it:
    //
    // 1. Toggling `panelLoader.active` on every click destroys and
    //    recreates the popup's whole window each time. That throws away
    //    the panel's own state (selected entry, in-flight draft) on every
    //    close, and repeatedly tearing down/rebuilding a real window is the
    //    kind of thing Quickshell's window backends are not built for.
    // 2. PopupWindow positions itself via `anchor.item`/`anchor.window`;
    //    with neither set (as in the brief) it has nothing to position
    //    against. `visible: true` alone does not give it a place to appear.
    //
    // Fix: load the popup once and keep it loaded, forward this widget as
    // its anchor item, and let the popup own its own show/hide via
    // open()/close()/toggle() (same shape the queens/clock panels use)
    // instead of a `visible: true` hardcoded in the popup and a toggled
    // Loader.active driving it from outside.
    Loader {
        id: panelLoader
        active: true
        source: "Panel.qml"
        onLoaded: if (item) item.anchorItem = root
    }
}
