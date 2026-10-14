// BarWidget.qml
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// Bar pill for pending WhatsApp replies.
//
// Click opens the chat window (ChatWindow.qml, this plugin's `panel` entry
// point) on the "No leídos" tab. The pill itself is hidden whenever there is
// nothing pending and the bridge is healthy — it only needs to exist when
// there is something to do or something wrong.
BarWidget {
  id: root
  moduleName: "io.github.cristosolar.waassistant"

  property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
  property int pending: 0
  property bool bridgeOk: true

  readonly property bool visiblePill: pending > 0 || !bridgeOk

  visible: visiblePill
  implicitWidth: visiblePill ? button.implicitWidth : 0
  implicitHeight: visiblePill ? button.implicitHeight : 0

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

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.bridgeOk ? "WA " + root.pending : "WA ✕"
    active: !root.bridgeOk
    onPressed: function(b) {
      Quickshell.execDetached(["omarchy-shell", "shell", "summon", root.moduleName,
                               '{"filter":"unread"}'])
    }
  }
}
