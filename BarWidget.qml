// BarWidget.qml
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// Bar pill for pending WhatsApp replies, and host for the reply panel.
//
// Click toggles the panel. The pill itself is hidden whenever there is
// nothing pending and the bridge is healthy — it only needs to exist when
// there is something to do or something wrong.
BarWidget {
  id: root
  moduleName: "io.github.cristosolar.waassistant"

  property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
  property int pending: 0
  property bool bridgeOk: true

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool visiblePill: pending > 0 || !bridgeOk

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

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

  // Panel.qml is loaded once and kept loaded rather than toggled through
  // Loader.active — see Panel.qml's own header comment for why (this
  // mirrors the admin.clock and queens plugins, which do the same).
  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.bridgeOk ? "WA " + root.pending : "WA ✕"
    active: !root.bridgeOk
    onPressed: function(b) { root.togglePanel() }
  }
}
