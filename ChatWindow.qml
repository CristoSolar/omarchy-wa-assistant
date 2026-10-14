// ChatWindow.qml
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects
import QtMultimedia
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// The live chat window: every chat of both accounts on the left, the open
// conversation on the right, a composer under it.
//
// Loaded by the shell's panel Loader only while summoned
// (`omarchy-shell shell toggle io.github.cristosolar.waassistant '{…}'`) and
// destroyed when hidden, so closed it costs nothing. The shell calls
// open(payloadJson) after loading and close() before unloading; closing from
// inside goes through shell.hide() so the shell's own open/closed set stays
// right and the Loader really goes away.
//
// Everything it knows comes from the host CLIs, never from SQL or HTTP here:
// bin/wa-chat (list, pages, mark read), bin/wa-reply (the only sender, plus
// drafts and quick replies), bin/wa-media (attachments). Refresh is driven by
// the sink touching state/changed — no polling.
Item {
  id: root

  // ---- host injections ---------------------------------------------------
  property var shell: null
  property var manifest: null

  readonly property string pluginId: "io.github.cristosolar.waassistant"
  property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
  // Same resolution as sink/store.py's state_root()/data_dir(): the
  // environment variables first, then the default under ~/.local/share.
  readonly property string stateRoot: Quickshell.env("WA_STATE_ROOT")
      || (Quickshell.env("HOME") + "/.local/share/omarchy-wa")
  readonly property string dataDir: Quickshell.env("WA_DATA_DIR") || (root.stateRoot + "/state")

  // ---- theme -------------------------------------------------------------
  readonly property color fg: Color.popups.text
  readonly property color bg: Color.popups.background
  readonly property color accent: Color.accent
  readonly property color urgent: Color.urgent
  readonly property string fontFamily: Style.font.family

  // ---- lifecycle ---------------------------------------------------------
  property bool opened: false

  function open(payloadJson) {
    var p = {}
    try { p = payloadJson ? JSON.parse(String(payloadJson)) : {} } catch (e) { p = {} }
    if (p && typeof p.filter === "string"
        && ["all", "unread", "trabajo", "personal", "contacts"].indexOf(p.filter) !== -1)
      root.filter = p.filter
    // Summoned while open (shortcut, bar, notification): bring it forward,
    // wherever it is, instead of closing it.
    if (root.opened)
      Quickshell.execDetached(["hyprctl", "dispatch", 'hl.dsp.focus({ window = "title:^Omarchy WhatsApp$" })'])
    root.opened = true
    root.loadChats()
    root.loadContacts()
    // A chat named in the payload (a notification click) wins; otherwise
    // the last chat that was open comes back. Either way it is only SHOWN:
    // reading on the phone needs a click on its row (see openChat).
    var acc = (p && p.account) || ""
    var jid = (p && p.jid) || ""
    root.pendingRestored = false
    if (!acc || !jid) {
      var last = String(lastChatFile.text() || "").trim()
      var cut = last.indexOf(":")
      if (cut > 0) { acc = last.slice(0, cut); jid = last.slice(cut + 1); root.pendingRestored = true }
    }
    if (acc && jid && (acc !== root.curAcc || jid !== root.curJid))
      root.pendingOpen = acc + ":" + jid
    Qt.callLater(function() { searchField.forceActiveFocus() })
  }

  // Host-initiated close (shell hide, or a toggle). The window is about to
  // be destroyed: nothing is focused any more.
  function close() {
    audioPlayer.stop()
    focusedFile.setText("")
    root.opened = false
  }

  // User-initiated close (Esc, ✕): ask the shell, which calls close() and
  // unloads us.
  function requestClose() {
    if (root.shell && typeof root.shell.hide === "function") root.shell.hide(root.pluginId)
    else root.close()
  }

  Component.onDestruction: focusedFile.setText("")

  // ---- chat list ---------------------------------------------------------
  property var chats: []
  property bool offline: false
  property string listError: ""
  property string filter: "all"
  property string search: ""
  property bool settingsOpen: false
  // "acc:jid" to select once the list arrives (restored or from payload).
  property string pendingOpen: ""
  // pendingOpen came from window-last-chat, not from the payload.
  property bool pendingRestored: false

  readonly property var visibleChats: {
    var q = root.search.trim().toLowerCase()
    var qDigits = q.replace(/[^0-9]/g, "")
    var out = []
    var i
    for (i = 0; root.filter !== "contacts" && i < root.chats.length; i++) {
      var c = root.chats[i]
      if (root.filter === "unread" && !(c.unread > 0)) continue
      if ((root.filter === "trabajo" || root.filter === "personal") && c.account !== root.filter) continue
      if (q !== "") {
        var name = String(c.name || "").toLowerCase()
        var number = String(c.jid || "").split("@")[0]
        if (name.indexOf(q) === -1 && (qDigits === "" || number.indexOf(qDigits) === -1)) continue
      }
      out.push(c)
    }
    // Starting a chat: the address book on the Contactos tab, and on a
    // search in the other tabs; a typed number can always be written to.
    if (root.filter === "unread") return out
    var accs = (root.filter === "trabajo" || root.filter === "personal") ? [root.filter] : null
    if (q !== "" || root.filter === "contacts") {
      var have = {}
      for (i = 0; i < root.chats.length; i++) {
        var ch = root.chats[i]
        have[ch.account + "|" + String(ch.name || "").toLowerCase()] = true
        have[ch.account + "|" + String(ch.jid).split("@")[0]] = true
      }
      for (i = 0; i < root.contacts.length; i++) {
        var k = root.contacts[i]
        if (accs && accs.indexOf(k.account) === -1) continue
        if (have[k.account + "|" + k.name.toLowerCase()] || have[k.account + "|" + k.phone]) continue
        if (q !== "" && k.name.toLowerCase().indexOf(q) === -1
            && (qDigits === "" || k.phone.indexOf(qDigits) === -1)) continue
        out.push({ kind: "contact", account: k.account, label: k.label, name: k.name,
                   phone: k.phone, last_text: "+" + k.phone })
      }
    }
    if (qDigits.length >= 8 && q.replace(/[0-9+\s()-]/g, "") === "") {
      var labels = {}
      for (i = 0; i < root.contacts.length; i++) labels[root.contacts[i].account] = root.contacts[i].label
      for (i = 0; i < root.chats.length; i++) labels[root.chats[i].account] = root.chats[i].label
      for (var a in labels) {
        if (accs && accs.indexOf(a) === -1) continue
        out.push({ kind: "number", account: a, label: labels[a], name: "Escribir a +" + qDigits,
                   phone: qDigits, last_text: "Nuevo chat por " + labels[a] })
      }
    }
    return out
  }

  // ---- attachments -------------------------------------------------------
  // 📎 opens the same categories as WhatsApp. Files come from the system
  // picker (zenity); everything goes out through `wa-reply chat-attach`,
  // under the same double-send guard as text.
  property string attachMode: ""  // "", menu, media, location, contact, poll
  property var attachFiles: []
  property var attachQueue: []
  property var attachAckArgs: null
  property string attachSearch: ""
  ListModel { id: pollOptions }

  function openAttach(mode) {
    root.attachMode = mode
    root.status = ""
    root.statusWarn = false
    if (mode === "photos" || mode === "document") { root.attachMode = "menu"; root.pickFiles(mode) }
    if (mode === "poll") { pollOptions.clear(); pollOptions.append({ v: "" }); pollOptions.append({ v: "" }) }
    if (mode === "contact") root.attachSearch = ""
  }
  function pickFiles(kind) {
    if (filePicker.running) return
    var cmd = ["zenity", "--file-selection", "--multiple", "--separator=\n",
               "--title=" + (kind === "photos" ? "Fotos y videos" : "Documentos")]
    if (kind === "photos")
      cmd.push("--file-filter=Fotos y videos | *.jpg *.jpeg *.png *.webp *.gif *.heic *.mp4 *.mov *.m4v *.3gp *.mkv *.webm *.JPG *.JPEG *.PNG *.MP4 *.MOV")
    filePicker.command = cmd
    filePicker.running = true
  }
  Process {
    id: filePicker
    stdout: StdioCollector {
      onStreamFinished: {
        var files = String(text).split("\n").filter(function(f) { return f.trim() !== "" })
        if (files.length === 0) return  // cancelled: the menu stays
        root.attachFiles = files
        root.attachMode = "media"
      }
    }
  }
  function parseLatLon(t) {
    var m = String(t).match(/(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)/)
    return m ? [m[1], m[2]] : null
  }
  readonly property var attachContacts: {
    if (root.attachMode !== "contact") return []
    var q = root.attachSearch.trim().toLowerCase(), out = []
    for (var i = 0; i < root.contacts.length && out.length < 40; i++) {
      var c = root.contacts[i]
      if (c.account !== root.curAcc) continue
      if (q !== "" && c.name.toLowerCase().indexOf(q) === -1 && c.phone.indexOf(q) === -1) continue
      out.push(c)
    }
    return out
  }
  // jobs: [[args...], ...] sent one after another.
  function runAttach(jobs, acknowledged) {
    if (root.sending || root.drafting || !root.curJid || jobs.length === 0) return
    root.resetComposer()
    root.attachAckArgs = null
    root.attachQueue = jobs.slice(1)
    root.startAttach(jobs[0], acknowledged)
  }
  function startAttach(args, acknowledged) {
    root.sending = true
    root.status = "Enviando adjunto…" + (root.attachQueue.length > 0 ? " (quedan " + root.attachQueue.length + ")" : "")
    attachProc.key = root.curKey
    attachProc.args = args
    attachProc.gotAnswer = false
    var cmd = [root.pluginDir + "bin/wa-reply", "chat-attach", root.curAcc, root.curJid].concat(args)
    if (root.curNew) cmd.push("--new")
    if (acknowledged) cmd.push("--acknowledged")
    attachProc.command = cmd
    attachProc.running = true
  }
  Process {
    id: attachProc
    property string key: ""
    property var args: []
    property bool gotAnswer: false
    stdout: StdioCollector {
      onStreamFinished: { attachProc.gotAnswer = true; root.onAttached(text, attachProc.key, attachProc.args) }
    }
    onExited: if (!attachProc.gotAnswer) root.onAttached("", attachProc.key, attachProc.args)
  }
  function onAttached(out, key, args) {
    root.sending = false
    if (key !== root.curKey) return
    var r = null
    try { r = JSON.parse(out) } catch (e) { r = null }
    if (r && r.ok) {
      root.fetchMessages(0)
      if (r.dry_run) { root.status = "DRY RUN: no se envió nada."; root.statusWarn = true }
      if (root.attachQueue.length > 0) { var next = root.attachQueue.shift(); root.startAttach(next, false); return }
      if (!r.dry_run) root.status = ""
      root.attachMode = ""
      root.attachFiles = []
      return
    }
    root.attachQueue = []
    if (r && r.needs_ack) {
      root.ackMessage = r.error || "Un envío anterior quedó sin confirmación."
      root.attachAckArgs = args
    } else if (!r || r.unknown) {
      root.status = "Sin confirmación: puede que el adjunto ya haya salido. Revisá WhatsApp antes de reintentar."
      root.statusWarn = true
    } else {
      root.status = "No salió: " + (r.error || "error desconocido")
      root.statusWarn = true
    }
  }

  // ---- @mentions ----------------------------------------------------------
  // In a group, "@" in the composer suggests its members. A picked member
  // shows as "@Name"; on send each "@Name" becomes "@<number>" plus a
  // --mention, which is what makes WhatsApp treat it as a real mention.
  property var members: []
  property var mentionMap: ({})  // "Name" -> jid, for this chat's composer
  property string mentionQuery: ""
  property bool mentionOpen: false
  property int mentionIndex: 0
  readonly property var mentionMatches: {
    if (!root.mentionOpen) return []
    var q = root.mentionQuery.toLowerCase(), out = []
    for (var i = 0; i < root.members.length && out.length < 6; i++)
      if (root.members[i].name.toLowerCase().indexOf(q) !== -1) out.push(root.members[i])
    return out
  }

  function loadMembers() {
    root.members = []
    if (!root.curGroup) return
    membersProc.key = root.curKey
    membersProc.command = [root.pluginDir + "bin/wa-chat", "members", root.curAcc, root.curJid]
    membersProc.running = true
  }
  Process {
    id: membersProc
    property string key: ""
    stdout: StdioCollector {
      onStreamFinished: {
        if (membersProc.key !== root.curKey) return
        var d = null
        try { d = JSON.parse(text) } catch (e) { d = null }
        root.members = d && Array.isArray(d.members) ? d.members : []
      }
    }
  }

  // Called on every edit: is the cursor right after "@something"?
  function updateMention() {
    var before = composer.text.slice(0, composer.cursorPosition)
    var m = root.curGroup ? before.match(/(^|\s)@([^\s@]*)$/) : null
    root.mentionOpen = m !== null && root.members.length > 0
    root.mentionQuery = m ? m[2] : ""
    root.mentionIndex = 0
  }
  function pickMention(member) {
    var pos = composer.cursorPosition
    var start = pos - root.mentionQuery.length - 1  // the "@"
    composer.remove(start, pos)
    composer.insert(start, "@" + member.name + " ")
    root.mentionMap[member.name] = member.jid
    root.mentionOpen = false
    composer.forceActiveFocus()
  }
  // [text to send, [jids]] from what the composer shows.
  function withMentions(text) {
    var jids = []
    // Longest first: "@Ana María" must not be eaten by "@Ana".
    var names = Object.keys(root.mentionMap).sort(function(a, b) { return b.length - a.length })
    for (var k = 0; k < names.length; k++) {
      var name = names[k]
      if (text.indexOf("@" + name) === -1) continue
      var jid = root.mentionMap[name]
      text = text.split("@" + name).join("@" + jid.split("@")[0])
      jids.push(jid)
    }
    return [text, jids]
  }

  // ---- profile pictures -------------------------------------------------
  // `wa-chat avatars` caches them under avatars/<acc>/<name>.jpg (an empty
  // file = no picture). Only rows that get drawn ask, in batches; the
  // backend skips anything cached in the last week.
  property int avatarRev: 0
  property var avatarAsked: ({})
  property var avatarQueue: []

  function avatarUrl(acc, target) {
    var safe = String(target).replace(/[^A-Za-z0-9@._-]/g, "").replace(/^\.+/, "")
    return "file://" + root.stateRoot + "/avatars/" + acc + "/" + safe + ".jpg?" + root.avatarRev
  }
  function wantAvatar(acc, target) {
    var key = acc + "|" + target
    if (!acc || !target || root.avatarAsked[key]) return
    root.avatarAsked[key] = true
    root.avatarQueue.push(key)
    avatarTimer.restart()
  }
  Timer {
    id: avatarTimer; interval: 400
    onTriggered: {
      if (avatarProc.running || root.avatarQueue.length === 0) return
      avatarProc.command = [root.pluginDir + "bin/wa-chat", "avatars"].concat(root.avatarQueue.splice(0, 20))
      avatarProc.running = true
    }
  }
  Process {
    id: avatarProc
    onExited: {
      root.avatarRev++
      if (root.avatarQueue.length > 0) avatarTimer.restart()
    }
  }

  component Avatar: Item {
    id: av
    property string acc: ""
    property string target: ""
    property string name: ""
    property int size: 36
    implicitWidth: av.size
    implicitHeight: av.size
    onTargetChanged: root.wantAvatar(av.acc, av.target)
    Component.onCompleted: root.wantAvatar(av.acc, av.target)

    Rectangle {
      anchors.fill: parent
      radius: width / 2
      visible: avImg.status !== Image.Ready
      color: Style.normalFillFor(root.fg, root.accent)
      Text {
        anchors.centerIn: parent
        text: { var m = String(av.name).match(/[A-Za-zÀ-ÿ0-9]/); return m ? m[0].toUpperCase() : "?" }
        color: root.fg
        font.family: root.fontFamily
        font.pixelSize: av.size * 0.42
        font.bold: true
      }
    }
    Image {
      id: avImg
      anchors.fill: parent
      visible: false
      source: av.target ? root.avatarUrl(av.acc, av.target) : ""
      cache: false
      asynchronous: true
      sourceSize: Qt.size(av.size * 2, av.size * 2)
    }
    Rectangle { id: avMask; anchors.fill: parent; radius: width / 2; visible: false; layer.enabled: true }
    MultiEffect {
      anchors.fill: parent
      visible: avImg.status === Image.Ready
      source: avImg
      maskEnabled: true
      maskSource: avMask
      maskThresholdMin: 0.5
      maskSpreadAtMin: 1.0
    }
  }

  // ---- starting a chat ---------------------------------------------------
  property var contacts: []
  property string newError: ""

  function loadContacts() {
    if (contactsProc.running) return
    contactsProc.command = [root.pluginDir + "bin/wa-chat", "contacts"]
    contactsProc.running = true
  }
  Process {
    id: contactsProc
    stdout: StdioCollector {
      onStreamFinished: {
        var d = null
        try { d = JSON.parse(text) } catch (e) { d = null }
        if (d && Array.isArray(d.contacts)) root.contacts = d.contacts
      }
    }
  }

  // WhatsApp confirms the number and names the jid it uses; a chat that
  // already exists under that jid opens instead of a duplicate.
  function startChat(c) {
    if (resolveProc.running || root.sending || root.drafting) return
    root.newError = ""
    resolveProc.item = c
    resolveProc.command = [root.pluginDir + "bin/wa-chat", "resolve", c.account, c.phone]
    resolveProc.running = true
  }
  Process {
    id: resolveProc
    property var item: null
    stdout: StdioCollector {
      onStreamFinished: {
        var c = resolveProc.item
        var d = null
        try { d = JSON.parse(text) } catch (e) { d = null }
        if (!d || d.error || !d.jid) {
          root.newError = (d && d.error) ? d.error : "wa-chat no respondió"
          return
        }
        var existing = root.findChat(c.account, d.jid)
        if (existing) { root.openChat(existing, true); return }
        root.openChat({ account: c.account, label: c.label, jid: d.jid, group: false, isNew: true,
                        name: c.kind === "contact" ? c.name : (d.name || "+" + c.phone) }, false)
      }
    }
  }

  function findChat(acc, jid) {
    for (var i = 0; i < root.chats.length; i++)
      if (root.chats[i].account === acc && root.chats[i].jid === jid) return root.chats[i]
    return null
  }

  property bool chatsAgain: false
  function loadChats() {
    if (chatsProc.running) { root.chatsAgain = true; return }
    chatsProc.command = [root.pluginDir + "bin/wa-chat", "chats"]
    chatsProc.running = true
  }

  Process {
    id: chatsProc
    stdout: StdioCollector { onStreamFinished: root.onChats(text) }
    onExited: if (root.chatsAgain) { root.chatsAgain = false; root.loadChats() }
  }

  // `database is locked`: keep what we have, look again in 2 s.
  Timer { id: busyRetry; interval: 2000; onTriggered: root.loadChats() }

  function onChats(text) {
    var d = null
    try { d = JSON.parse(text) } catch (e) { d = null }
    if (!d) { root.listError = "wa-chat no respondió"; return }
    if (d.error) {
      if (d.error === "ocupada") { busyRetry.restart(); return }
      root.listError = d.error === "esquema" ? "Historial no disponible" : d.error
      return
    }
    root.chats = Array.isArray(d.chats) ? d.chats : []
    root.offline = d.offline === true
    root.listError = ""
    if (root.pendingOpen !== "") {
      var key = root.pendingOpen
      root.pendingOpen = ""
      var cut = key.indexOf(":")
      var c = root.findChat(key.slice(0, cut), key.slice(cut + 1))
      // Opened on "No leídos": a remembered chat with nothing unread would
      // only be noise next to that list.
      if (c && !(root.pendingRestored && root.filter === "unread" && !(c.unread > 0)))
        root.openChat(c, false)
    } else if (root.curJid !== "") {
      var cur = root.findChat(root.curAcc, root.curJid)
      if (cur) {
        root.curName = cur.name || root.curName
        // New messages in the chat being looked at count as read, but only
        // in a chat the user clicked open this session, while the window is
        // active and the conversation is actually visible.
        if (cur.unread > 0 && root.curExplicit && root.windowActive && !root.settingsOpen)
          root.markRead(cur)
      }
    }
  }

  // ---- open conversation -------------------------------------------------
  property string curAcc: ""
  property string curJid: ""
  property string curName: ""
  property string curLabel: ""
  property bool curGroup: false
  // A chat the owner is starting: no history yet, sends carry --new.
  property bool curNew: false
  readonly property string curKey: root.curJid ? root.curAcc + ":" + root.curJid : ""
  property bool msgMore: false
  property string msgError: ""
  property var quickReplies: []

  property bool curExplicit: false
  // "acc:jid@last_id" of the last read attempt. One attempt per newest
  // message: a read that fails without raising the marker (ocupada, no
  // account, offline) can't turn read → chats → read into a loop.
  property string readTriedAt: ""

  ListModel { id: msgModel }

  // explicit: the user clicked this chat's row. Only then is it marked read
  // on the phone. Refused while a send or draft is in flight, so their
  // outcome (and the text in the box) is never lost to a chat switch.
  function openChat(c, explicit) {
    if (root.sending || root.drafting) return
    root.curExplicit = explicit === true
    root.curAcc = c.account
    root.curJid = c.jid
    root.curName = c.name || c.jid
    root.curLabel = c.label || c.account
    root.curGroup = c.group === true
    root.curNew = c.isNew === true
    msgModel.clear()
    root.msgMore = false
    root.msgError = ""
    root.media = ({})
    audioPlayer.stop()
    root.playingId = ""
    root.resetComposer()
    composer.text = ""
    root.quickReplies = []
    root.mentionMap = ({})
    root.mentionOpen = false
    root.attachMode = ""
    root.attachFiles = []
    root.attachQueue = []
    root.attachAckArgs = null
    root.loadMembers()
    root.fetchMessages(0)
    if (root.curExplicit) root.markRead(c, true)
    root.loadQuick()
    lastChatFile.setText(root.curKey)
    Qt.callLater(function() { composer.forceActiveFocus() })
  }

  // before = 0 → newest page (also used to pick up new messages);
  // before = id → the 50 older than that.
  property int msgQueuedBefore: -1
  function fetchMessages(before) {
    if (!root.curJid) return
    if (msgProc.running) { root.msgQueuedBefore = before; return }
    msgProc.key = root.curKey
    msgProc.before = before
    var cmd = [root.pluginDir + "bin/wa-chat", "messages", root.curAcc, root.curJid]
    if (before > 0) cmd = cmd.concat(["--before", String(before)])
    msgProc.command = cmd
    msgProc.running = true
  }

  function loadOlder() {
    if (!root.msgMore || msgProc.running || msgModel.count === 0) return
    root.fetchMessages(msgModel.get(msgModel.count - 1).mid)
  }

  Process {
    id: msgProc
    property string key: ""
    property int before: 0
    stdout: StdioCollector { onStreamFinished: root.onMessages(text, msgProc.key, msgProc.before) }
    onExited: {
      if (root.msgQueuedBefore >= 0) {
        var b = root.msgQueuedBefore
        root.msgQueuedBefore = -1
        root.fetchMessages(b)
      } else {
        topCheck.restart()
      }
    }
  }

  // After a page lands, if the top is still in view (short chat, or the
  // user is parked at the top) keep paging.
  Timer {
    id: topCheck; interval: 120
    onTriggered: if (msgList.atTop) root.loadOlder()
  }

  function rowFor(m) {
    var a = m.attachment || null
    return {
      mid: Number(m.id), messageId: String(m.message_id || ""),
      fromMe: m.from_me === true, senderName: String(m.sender_name || m.push_name || ""),
      ts: String(m.ts || ""), text: String(m.text || ""),
      atype: a ? String(a.type || "") : "", adown: a ? a.downloadable === true : false
    }
  }

  function onMessages(text, key, before) {
    if (key !== root.curKey) return
    var d = null
    try { d = JSON.parse(text) } catch (e) { d = null }
    if (!d) { root.msgError = "wa-chat no respondió"; return }
    if (d.error) {
      if (d.error === "ocupada") return  // keep what is shown
      root.msgError = d.error === "esquema" ? "Historial no disponible" : d.error
      return
    }
    root.msgError = ""
    var msgs = Array.isArray(d.messages) ? d.messages : []  // old → new
    var i
    // Model index 0 is the newest message (the list grows bottom-to-top).
    if (before > 0) {
      for (i = msgs.length - 1; i >= 0; i--) msgModel.append(root.rowFor(msgs[i]))
      root.msgMore = d.more === true
      return
    }
    var top = msgModel.count > 0 ? msgModel.get(0).mid : -1
    var newer = []
    for (i = 0; i < msgs.length; i++) if (Number(msgs[i].id) > top) newer.push(msgs[i])
    if (msgModel.count === 0 || (newer.length === msgs.length && msgs.length > 0)) {
      // First page, or more arrived than one page holds: start over.
      msgModel.clear()
      for (i = msgs.length - 1; i >= 0; i--) msgModel.append(root.rowFor(msgs[i]))
      root.msgMore = d.more === true
    } else {
      for (i = 0; i < newer.length; i++) msgModel.insert(0, root.rowFor(newer[i]))
    }
  }

  // Opening a chat marks it read on the phone (owner's decision).
  // force: a click on the row (bounded by the user) may retry the same
  // attempt; the automatic path may not.
  function markRead(c, force) {
    if (!root.curJid || readProc.running) return
    var attempt = root.curKey + "@" + String(c && c.last_id !== undefined ? c.last_id : "")
    if (attempt === root.readTriedAt && !force) return
    root.readTriedAt = attempt
    readProc.command = [root.pluginDir + "bin/wa-chat", "read", root.curAcc, root.curJid]
    readProc.running = true
  }
  Process {
    id: readProc
    // The list's unread count follows from read.json; ask again.
    onExited: root.loadChats()
  }

  function loadQuick() {
    if (quickProc.running || !root.curAcc) return
    quickProc.acc = root.curAcc
    quickProc.command = [root.pluginDir + "bin/wa-reply", "quick-get", root.curAcc]
    quickProc.running = true
  }
  Process {
    id: quickProc
    property string acc: ""
    stdout: StdioCollector {
      onStreamFinished: {
        if (quickProc.acc !== root.curAcc) return
        var d = null
        try { d = JSON.parse(text) } catch (e) { d = null }
        root.quickReplies = d && Array.isArray(d.quick_replies) ? d.quick_replies : []
      }
    }
  }

  // ---- refresh on state/changed -----------------------------------------
  FileView {
    id: changedFile
    path: root.dataDir + "/changed"
    watchChanges: true
    printErrors: false
    onFileChanged: changedDebounce.restart()
  }
  Timer {
    id: changedDebounce; interval: 150
    onTriggered: {
      root.loadChats()
      if (root.curJid) root.fetchMessages(0)
    }
  }

  // ---- state/focused and the remembered chat ----------------------------
  readonly property bool windowActive: content.Window.active === true
  readonly property string focusedKey: (root.opened && root.windowActive && !root.settingsOpen) ? root.curKey : ""
  onFocusedKeyChanged: focusedFile.setText(root.focusedKey)

  FileView {
    id: focusedFile
    path: root.dataDir + "/focused"
    blockWrites: true
    printErrors: false
  }
  FileView {
    id: lastChatFile
    path: root.stateRoot + "/window-last-chat"
    blockLoading: true
    blockWrites: true
    printErrors: false
  }

  // ---- media -------------------------------------------------------------
  // message_id → {state: "loading"|"ready"|"error", path, error}
  property var media: ({})
  property string playingId: ""

  function setMedia(id, v) {
    var next = ({})
    for (var k in root.media) next[k] = root.media[k]
    next[id] = v
    root.media = next
  }

  function mediaLabel(atype) {
    var labels = { "image": "📷 Foto", "sticker": "📷 Sticker", "audio": "🎤 Audio",
                   "video": "🎬 Video", "document": "📄 Documento",
                   "ubicación": "📍 Ubicación", "contacto": "👤 Contacto" }
    return labels[atype] || ("📎 " + atype)
  }

  function formatDuration(ms) {
    if (!ms || ms <= 0) return "0:00"
    var s = Math.floor(ms / 1000)
    return Math.floor(s / 60) + ":" + ((s % 60) < 10 ? "0" : "") + (s % 60)
  }

  function mediaClicked(messageId, atype) {
    var m = root.media[messageId]
    if (m && m.state === "ready") {
      if (atype === "audio") root.toggleAudio(messageId, m.path)
      else Quickshell.execDetached(["xdg-open", m.path])
      return
    }
    if (mediaProc.running) return
    root.setMedia(messageId, { state: "loading", path: "", error: "" })
    mediaProc.key = root.curKey
    mediaProc.messageId = messageId
    mediaProc.atype = atype
    mediaProc.gotAnswer = false
    mediaProc.command = [root.pluginDir + "bin/wa-media", "get-msg", root.curAcc, messageId]
    mediaProc.running = true
  }

  Process {
    id: mediaProc
    property string key: ""
    property string messageId: ""
    property string atype: ""
    property bool gotAnswer: false
    stdout: StdioCollector {
      onStreamFinished: {
        mediaProc.gotAnswer = true
        if (mediaProc.key !== root.curKey) return
        var d = null
        try { d = JSON.parse(text) } catch (e) { d = null }
        if (d && d.ok && d.path) {
          root.setMedia(mediaProc.messageId, { state: "ready", path: d.path, error: "" })
          if (mediaProc.atype === "audio") root.toggleAudio(mediaProc.messageId, d.path)
          else if (mediaProc.atype !== "image" && mediaProc.atype !== "sticker")
            Quickshell.execDetached(["xdg-open", d.path])
        } else {
          root.setMedia(mediaProc.messageId, { state: "error", path: "",
                        error: (d && d.error) ? d.error : "no se pudo descargar" })
        }
      }
    }
    onExited: function(exitCode) {
      if (mediaProc.gotAnswer || mediaProc.key !== root.curKey) return
      root.setMedia(mediaProc.messageId, { state: "error", path: "",
                    error: "wa-media terminó sin responder (código " + exitCode + ")" })
    }
  }

  function toggleAudio(messageId, path) {
    if (root.playingId === messageId) {
      if (audioPlayer.playbackState === MediaPlayer.PlayingState) audioPlayer.pause()
      else audioPlayer.play()
      return
    }
    audioPlayer.stop()
    root.playingId = messageId
    audioPlayer.source = "file://" + path
    audioPlayer.play()
  }

  MediaPlayer {
    id: audioPlayer
    audioOutput: AudioOutput {}
    onErrorOccurred: function(error, errorString) {
      if (root.playingId !== "")
        root.setMedia(root.playingId, { state: "error", path: "",
                      error: "no se pudo reproducir; probá de nuevo" })
      root.playingId = ""
    }
  }

  // ---- composer ----------------------------------------------------------
  property bool sending: false
  property bool drafting: false
  property string status: ""
  property bool statusWarn: false
  // Last send that failed outright: the "No salió" + Reintentar state.
  property string failedText: ""
  // wa-reply refused because an earlier send's outcome is unknown.
  property string ackMessage: ""
  property string ackText: ""

  function resetComposer() {
    root.status = ""
    root.statusWarn = false
    root.failedText = ""
    root.ackMessage = ""
    root.ackText = ""
    root.drafting = false
  }

  function doSend(text, acknowledged) {
    text = String(text || "")
    if (root.sending || root.drafting || !root.curJid || text.trim() === "") return
    root.resetComposer()
    root.sending = true
    root.status = "Enviando…"
    sendProc.key = root.curKey
    sendProc.text = text
    sendProc.gotAnswer = false
    var wm = root.withMentions(text)
    var cmd = [root.pluginDir + "bin/wa-reply", "chat-send", root.curAcc, root.curJid, "--text=" + wm[0]]
    for (var i = 0; i < wm[1].length; i++) cmd.push("--mention=" + wm[1][i])
    if (acknowledged) cmd.push("--acknowledged")
    if (root.curNew) cmd.push("--new")
    sendProc.command = cmd
    sendProc.running = true
  }

  Process {
    id: sendProc
    property string key: ""
    property string text: ""
    property bool gotAnswer: false
    // Refusals exit 1 with JSON on stdout: parsed the same as success.
    stdout: StdioCollector {
      onStreamFinished: {
        sendProc.gotAnswer = true
        root.onSent(text, sendProc.key, sendProc.text)
      }
    }
    onExited: function(exitCode) {
      if (!sendProc.gotAnswer) root.onSent("", sendProc.key, sendProc.text)
    }
  }

  function onSent(out, key, text) {
    root.sending = false
    if (key !== root.curKey) return
    var r = null
    try { r = JSON.parse(out) } catch (e) { r = null }
    if (r && r.ok && r.dry_run) {
      root.status = "DRY RUN: no se envió nada."
      root.statusWarn = true
    } else if (r && r.ok) {
      if (composer.text === text) composer.text = ""
      root.status = ""
      root.fetchMessages(0)
    } else if (r && r.needs_ack) {
      root.ackMessage = r.error || "Un envío anterior quedó sin confirmación."
      root.ackText = text
    } else if (!r || r.unknown) {
      // Timed out or crashed mid-send: it may have gone out.
      root.status = "Sin confirmación: puede que ya haya salido. Revisá WhatsApp antes de reintentar."
      root.statusWarn = true
    } else {
      root.failedText = text
      root.status = "No salió: " + (r.error || "error desconocido")
      root.statusWarn = true
    }
  }

  // Ctrl+Enter: Claude writes a draft into the box. Never sends.
  function requestDraft() {
    var idea = composer.text.trim()
    if (root.drafting || root.sending || !root.curJid || idea === "") return
    root.resetComposer()
    root.drafting = true
    root.status = "Redactando…"
    draftProc.key = root.curKey
    draftProc.command = [root.pluginDir + "bin/wa-reply", "chat-draft", root.curAcc, root.curJid, "--idea=" + idea]
    if (root.curNew) draftProc.command = draftProc.command.concat(["--new"])
    draftProc.running = true
  }

  Process {
    id: draftProc
    property string key: ""
    stdout: StdioCollector {
      onStreamFinished: {
        root.drafting = false
        if (draftProc.key !== root.curKey) return
        var d = null
        try { d = JSON.parse(text) } catch (e) { d = null }
        if (d && d.draft && !d.error) {
          composer.text = d.draft
          composer.cursorPosition = composer.text.length
          root.status = "Borrador listo: revisalo y Enter para enviar."
          root.statusWarn = false
        } else {
          root.status = "No se pudo redactar: " + ((d && d.error) ? d.error : "sin respuesta")
          root.statusWarn = true
        }
        composer.forceActiveFocus()
      }
    }
    onExited: if (root.drafting) { root.drafting = false; root.status = "No se pudo redactar"; root.statusWarn = true }
  }

  function shortTime(ts) {
    // "2026-10-13T22:16:03-03:00" → today "22:16", otherwise "13/10 22:16"
    var s = String(ts || "")
    if (s.length < 16) return ""
    var now = new Date()
    var today = now.getFullYear() + "-" + ("0" + (now.getMonth() + 1)).slice(-2) + "-" + ("0" + now.getDate()).slice(-2)
    var hm = s.slice(11, 16)
    return s.slice(0, 10) === today ? hm : (s.slice(8, 10) + "/" + s.slice(5, 7) + " " + hm)
  }

  // ---- the window ------------------------------------------------------
  // A normal app window: Hyprland tiles it, it moves between workspaces and
  // sits next to other apps. Closing it (SUPER+W, ✕) unloads the plugin.
  FloatingWindow {
    id: window
    title: "Omarchy WhatsApp"  // unique: open() focuses it by this title
    visible: root.opened
    implicitWidth: 1040
    implicitHeight: 760
    minimumSize: Qt.size(640, 480)
    color: root.bg

    onVisibleChanged: if (!visible && root.opened) root.requestClose()

    Rectangle {
      id: content
      anchors.fill: parent
      color: root.bg
      focus: true

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.spacing.panelPadding
        spacing: Style.spacing.lg

        // -- header --
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.md
          Text {
            text: root.settingsOpen ? "WhatsApp · Configuración" : "WhatsApp"
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
            Layout.fillWidth: true
          }
          Button {
            text: root.settingsOpen ? "Volver" : "⚙"
            foreground: root.fg
            fontFamily: root.fontFamily
            bordered: true
            onClicked: {
              root.settingsOpen = !root.settingsOpen
              if (root.settingsOpen) settingsView.onShown()
              else root.loadChats()
            }
          }
          Button {
            text: "✕"
            foreground: root.fg
            fontFamily: root.fontFamily
            bordered: true
            onClicked: root.requestClose()
          }
        }

        // -- offline strip --
        Rectangle {
          visible: root.offline && !root.settingsOpen
          Layout.fillWidth: true
          implicitHeight: offlineText.implicitHeight + Style.spacing.md * 2
          color: Style.normalFillFor(root.urgent, root.urgent)
          border.width: Style.spacing.hairline
          border.color: root.urgent
          radius: Style.cornerRadius
          Text {
            id: offlineText
            anchors.centerIn: parent
            text: "Sin conexión: solo lectura"
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        SettingsView {
          id: settingsView
          visible: root.settingsOpen
          Layout.fillWidth: true
          Layout.fillHeight: true
          pluginDir: root.pluginDir
          stateRoot: root.stateRoot
          foreground: root.fg
          fontFamily: root.fontFamily
          urgent: root.urgent
        }

        RowLayout {
          visible: !root.settingsOpen
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: Style.spacing.xxl

          // ===== left: chat list =====
          ColumnLayout {
            Layout.preferredWidth: 340
            Layout.maximumWidth: 340
            Layout.fillHeight: true
            spacing: Style.spacing.md

            TextField {
              id: searchField
              Layout.fillWidth: true
              placeholderText: "Buscar, o un número para chat nuevo"
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              onTextChanged: root.search = text
              Keys.onEscapePressed: text = ""
            }

            Flow {
              Layout.fillWidth: true
              spacing: Style.spacing.sm
              Repeater {
                model: [ { id: "all", label: "Todos" }, { id: "unread", label: "No leídos" },
                         { id: "trabajo", label: "Trabajo" }, { id: "personal", label: "Personal" },
                         { id: "contacts", label: "Contactos" } ]
                delegate: Button {
                  required property var modelData
                  text: modelData.label
                  foreground: root.fg
                  fontFamily: root.fontFamily
                  fontSize: Style.font.bodySmall
                  bordered: true
                  selected: root.filter === modelData.id
                  onClicked: root.filter = modelData.id
                }
              }
            }

            Text {
              visible: root.listError !== ""
              text: "⚠ " + root.listError
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
              Layout.fillWidth: true
            }

            Text {
              visible: root.newError !== ""
              text: "⚠ " + root.newError
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
              Layout.fillWidth: true
            }

            Text {
              visible: root.listError === "" && root.visibleChats.length === 0
              text: root.chats.length === 0 ? "Todavía no hay chats en el historial."
                    : (root.filter === "unread" ? "Nada sin leer."
                       : (root.filter === "contacts" && root.contacts.length === 0 ? "Sin contactos sincronizados."
                          : "Ningún chat coincide."))
              color: Color.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
              Layout.fillWidth: true
            }

            ListView {
              id: chatList
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              spacing: Style.spacing.xs
              boundsBehavior: Flickable.StopAtBounds
              ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
              model: root.visibleChats
              delegate: Rectangle {
                id: chatRow
                required property var modelData
                readonly property bool isCurrent: chatRow.modelData.account === root.curAcc
                                                  && chatRow.modelData.jid === root.curJid
                width: ListView.view.width
                height: Math.max(chatCol.implicitHeight, rowAvatar.size) + Style.spacing.md * 2
                radius: Style.cornerRadius
                color: chatRow.isCurrent ? Style.selectedFillFor(root.fg, root.accent)
                       : (rowMouse.containsMouse ? Style.hoverFillFor(root.fg, root.accent) : "transparent")

                Avatar {
                  id: rowAvatar
                  anchors.left: parent.left; anchors.leftMargin: Style.spacing.md
                  anchors.verticalCenter: parent.verticalCenter
                  acc: chatRow.modelData.account
                  // A typed number gets no lookup: asking while you type is noise.
                  target: chatRow.modelData.kind === "number" ? ""
                          : (chatRow.modelData.kind === "contact" ? chatRow.modelData.phone : chatRow.modelData.jid)
                  name: chatRow.modelData.name || ""
                }

                ColumnLayout {
                  id: chatCol
                  anchors.left: rowAvatar.right; anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.spacing.md; anchors.rightMargin: Style.spacing.md
                  spacing: Style.spacing.xxs

                  RowLayout {
                    Layout.fillWidth: true
                    spacing: Style.spacing.sm
                    Text {
                      textFormat: Text.PlainText
                      text: (chatRow.modelData.kind === "contact" ? "👤 " : chatRow.modelData.kind === "number" ? "✏️ " : "")
                            + (chatRow.modelData.muted ? "🔕 " : "") + (chatRow.modelData.name || chatRow.modelData.jid)
                      color: root.fg
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.subtitle
                      font.bold: chatRow.modelData.unread > 0
                      elide: Text.ElideRight
                      Layout.fillWidth: true
                    }
                    Text {
                      text: root.shortTime(chatRow.modelData.last_ts)
                      color: Color.muted
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }
                  RowLayout {
                    Layout.fillWidth: true
                    spacing: Style.spacing.sm
                    Text {
                      textFormat: Text.PlainText
                      text: String(chatRow.modelData.last_text || "").replace(/\n/g, " ")
                      color: Color.muted
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideRight
                      Layout.fillWidth: true
                    }
                    Rectangle {
                      radius: Style.cornerRadius
                      color: Style.normalFillFor(root.fg, root.accent)
                      border.width: Style.spacing.hairline
                      border.color: Style.normalBorderFor(root.fg, root.accent)
                      implicitWidth: accText.implicitWidth + Style.spacing.md
                      implicitHeight: accText.implicitHeight + Style.spacing.xxs
                      Text {
                        id: accText
                        anchors.centerIn: parent
                        text: chatRow.modelData.label || chatRow.modelData.account
                        color: root.fg
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                      }
                    }
                    Rectangle {
                      visible: chatRow.modelData.unread > 0
                      radius: height / 2
                      color: root.accent
                      implicitWidth: Math.max(implicitHeight, unreadText.implicitWidth + Style.spacing.md)
                      implicitHeight: unreadText.implicitHeight + Style.spacing.xxs
                      Text {
                        id: unreadText
                        anchors.centerIn: parent
                        text: String(chatRow.modelData.unread)
                        color: root.bg
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                      }
                    }
                  }
                }
                MouseArea {
                  id: rowMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  // Blocked mid-send/draft: switching would drop the result.
                  enabled: !root.sending && !root.drafting
                  onClicked: {
                    root.settingsOpen = false
                    if (chatRow.modelData.kind) root.startChat(chatRow.modelData)
                    else root.openChat(chatRow.modelData, true)
                  }
                }
              }
            }
          }

          Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: Style.spacing.hairline
            color: Style.normalBorderFor(root.fg, root.accent)
          }

          // ===== right: conversation =====
          Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Text {
              visible: root.curJid === ""
              anchors.centerIn: parent
              text: "Elegí un chat"
              color: Color.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
            }

            ColumnLayout {
              visible: root.curJid !== ""
              anchors.fill: parent
              spacing: Style.spacing.md

              RowLayout {
                Layout.fillWidth: true
                spacing: Style.spacing.md
                Avatar {
                  acc: root.curAcc
                  target: root.curJid
                  name: root.curName
                  size: 32
                }
                Text {
                  textFormat: Text.PlainText
                  text: root.curName
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                  elide: Text.ElideRight
                  Layout.fillWidth: true
                }
                Text {
                  visible: root.curGroup
                  text: "Grupo"
                  color: Color.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Rectangle {
                  radius: Style.cornerRadius
                  color: Style.normalFillFor(root.fg, root.accent)
                  border.width: Style.spacing.hairline
                  border.color: Style.normalBorderFor(root.fg, root.accent)
                  implicitWidth: hdrAcc.implicitWidth + Style.spacing.lg
                  implicitHeight: hdrAcc.implicitHeight + Style.spacing.xs
                  Text {
                    id: hdrAcc
                    anchors.centerIn: parent
                    text: root.curLabel
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                }
              }

              Text {
                visible: root.msgError !== ""
                text: "⚠ " + root.msgError
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
                Layout.fillWidth: true
              }

              ListView {
                id: msgList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: Style.spacing.sm
                verticalLayoutDirection: ListView.BottomToTop
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                model: msgModel
                // Visual top: older messages are paged in from here.
                readonly property bool atTop: msgList.count > 0
                    && (msgList.visibleArea.heightRatio >= 1 || msgList.visibleArea.yPosition <= 0.001)
                onAtTopChanged: if (atTop) root.loadOlder()

                footer: Item {
                  width: ListView.view ? ListView.view.width : 0
                  height: root.msgMore ? olderText.implicitHeight + Style.spacing.md : 0
                  Text {
                    id: olderText
                    visible: root.msgMore
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "Cargando anteriores…"
                    color: Color.muted
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }

                delegate: Item {
                  id: msgRow
                  required property int mid
                  required property string messageId
                  required property bool fromMe
                  required property string senderName
                  required property string ts
                  required property string text
                  required property string atype
                  required property bool adown
                  readonly property var mstate: root.media[msgRow.messageId] || null
                  readonly property bool isImage: msgRow.atype === "image" || msgRow.atype === "sticker"
                  readonly property bool hasImage: msgRow.isImage && msgRow.mstate !== null && msgRow.mstate.state === "ready"
                  readonly property bool showText: msgRow.text !== "" && msgRow.text !== "[" + msgRow.atype + "]"
                  width: ListView.view.width
                  height: bubble.height

                  Rectangle {
                    id: bubble
                    readonly property real maxW: msgRow.width * 0.72
                    width: Math.min(maxW, Math.max(bubbleCol.implicitWidth, 80) + Style.spacing.lg * 2)
                    height: bubbleCol.implicitHeight + Style.spacing.md * 2
                    anchors.right: msgRow.fromMe ? parent.right : undefined
                    anchors.left: msgRow.fromMe ? undefined : parent.left
                    radius: Style.cornerRadius
                    color: msgRow.fromMe ? Style.selectedFillFor(root.fg, root.accent)
                                         : Style.normalFillFor(root.fg, root.accent)
                    border.width: Style.spacing.hairline
                    border.color: Style.normalBorderFor(root.fg, root.accent)

                    ColumnLayout {
                      id: bubbleCol
                      x: Style.spacing.lg
                      y: Style.spacing.md
                      width: bubble.width - Style.spacing.lg * 2
                      spacing: Style.spacing.xxs

                      Text {
                        visible: root.curGroup && !msgRow.fromMe && msgRow.senderName !== ""
                        textFormat: Text.PlainText
                        text: msgRow.senderName
                        color: root.accent
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        Layout.maximumWidth: bubble.maxW - Style.spacing.lg * 2
                        elide: Text.ElideRight
                      }

                      // attachment
                      Text {
                        visible: msgRow.atype !== "" && !msgRow.hasImage
                        textFormat: Text.PlainText
                        text: !msgRow.adown ? root.mediaLabel(msgRow.atype) + " (no disponible)"
                              : (msgRow.mstate && msgRow.mstate.state === "loading") ? root.mediaLabel(msgRow.atype) + " · descargando…"
                              : (msgRow.mstate && msgRow.mstate.state === "error") ? root.mediaLabel(msgRow.atype) + " · ⚠ " + msgRow.mstate.error + " (clic para reintentar)"
                              : (msgRow.atype === "audio" && root.playingId === msgRow.messageId)
                                ? ((audioPlayer.playbackState === MediaPlayer.PlayingState ? "⏸ " : "▶ ")
                                   + root.formatDuration(audioPlayer.position) + " / " + root.formatDuration(audioPlayer.duration))
                              : (msgRow.atype === "audio" ? "▶ " : "") + root.mediaLabel(msgRow.atype)
                        color: msgRow.adown ? root.fg : Color.muted
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.underline: msgRow.adown && mediaMouse.containsMouse
                        wrapMode: Text.Wrap
                        Layout.maximumWidth: bubble.maxW - Style.spacing.lg * 2
                        MouseArea {
                          id: mediaMouse
                          anchors.fill: parent
                          enabled: msgRow.adown
                          hoverEnabled: true
                          cursorShape: Qt.PointingHandCursor
                          onClicked: root.mediaClicked(msgRow.messageId, msgRow.atype)
                        }
                      }

                      Image {
                        visible: msgRow.hasImage
                        source: msgRow.hasImage ? "file://" + msgRow.mstate.path : ""
                        asynchronous: true
                        fillMode: Image.PreserveAspectFit
                        sourceSize.width: 320
                        sourceSize.height: 320
                        Layout.preferredWidth: Math.min(320, bubble.maxW - Style.spacing.lg * 2)
                        Layout.preferredHeight: status === Image.Ready && implicitWidth > 0
                            ? Layout.preferredWidth * implicitHeight / implicitWidth : 160
                        MouseArea {
                          anchors.fill: parent
                          cursorShape: Qt.PointingHandCursor
                          onClicked: Quickshell.execDetached(["xdg-open", msgRow.mstate.path])
                        }
                      }

                      Text {
                        visible: msgRow.showText
                        textFormat: Text.PlainText
                        text: msgRow.text
                        color: root.fg
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        wrapMode: Text.Wrap
                        Layout.maximumWidth: bubble.maxW - Style.spacing.lg * 2
                        Layout.fillWidth: true
                      }

                      Text {
                        text: root.shortTime(msgRow.ts)
                        color: Color.muted
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        Layout.alignment: Qt.AlignRight
                      }
                    }
                  }
                }
              }

              // -- needs_ack confirmation --
              Rectangle {
                visible: root.ackMessage !== ""
                Layout.fillWidth: true
                implicitHeight: ackCol.implicitHeight + Style.spacing.md * 2
                color: Style.normalFillFor(root.urgent, root.urgent)
                border.width: Style.spacing.hairline
                border.color: root.urgent
                radius: Style.cornerRadius
                ColumnLayout {
                  id: ackCol
                  anchors.left: parent.left; anchors.right: parent.right
                  anchors.top: parent.top; anchors.margins: Style.spacing.md
                  spacing: Style.spacing.sm
                  Text {
                    textFormat: Text.PlainText
                    text: "⚠ " + root.ackMessage
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                  }
                  RowLayout {
                    spacing: Style.spacing.md
                    Button {
                      text: "Revisé WhatsApp, enviar igual"
                      foreground: root.fg
                      fontFamily: root.fontFamily
                      bordered: true
                      enabled: !root.sending
                      onClicked: root.attachAckArgs ? root.runAttach([root.attachAckArgs], true)
                                                    : root.doSend(root.ackText, true)
                    }
                    Button {
                      text: "Cancelar"
                      foreground: root.fg
                      fontFamily: root.fontFamily
                      bordered: true
                      onClicked: { root.ackMessage = ""; root.ackText = ""; root.attachAckArgs = null }
                    }
                  }
                }
              }

              // -- quick replies --
              Flow {
                visible: root.quickReplies.length > 0
                Layout.fillWidth: true
                spacing: Style.spacing.sm
                Repeater {
                  model: root.quickReplies
                  delegate: Button {
                    required property string modelData
                    text: modelData.length > 40 ? modelData.slice(0, 39) + "…" : modelData
                    tooltipText: "Envía al toque: " + modelData
                    foreground: root.fg
                    fontFamily: root.fontFamily
                    fontSize: Style.font.bodySmall
                    bordered: true
                    enabled: !root.sending && !root.drafting
                    onClicked: root.doSend(modelData, false)
                  }
                }
              }

              // -- attachments --
              Rectangle {
                visible: root.attachMode !== ""
                Layout.fillWidth: true
                implicitHeight: attachCol.implicitHeight + Style.spacing.md * 2
                color: root.bg
                border.width: Style.spacing.hairline
                border.color: Style.normalBorderFor(root.fg, root.accent)
                radius: Style.cornerRadius
                ColumnLayout {
                  id: attachCol
                  anchors.left: parent.left; anchors.right: parent.right
                  anchors.top: parent.top; anchors.margins: Style.spacing.md
                  spacing: Style.spacing.sm

                  RowLayout {
                    Layout.fillWidth: true
                    Text {
                      text: ({ menu: "Adjuntar", media: "Fotos, videos y documentos", location: "Ubicación",
                               contact: "Contacto", poll: "Encuesta" })[root.attachMode] || ""
                      color: root.fg
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                      Layout.fillWidth: true
                    }
                    Button {
                      visible: root.attachMode !== "menu"
                      text: "‹ Volver"
                      foreground: root.fg; fontFamily: root.fontFamily; fontSize: Style.font.bodySmall
                      enabled: !root.sending
                      onClicked: root.attachMode = "menu"
                    }
                    Button {
                      text: "✕"
                      foreground: root.fg; fontFamily: root.fontFamily; fontSize: Style.font.bodySmall
                      enabled: !root.sending
                      onClicked: { root.attachMode = ""; root.attachFiles = [] }
                    }
                  }

                  // categories, as in WhatsApp
                  Flow {
                    visible: root.attachMode === "menu"
                    Layout.fillWidth: true
                    spacing: Style.spacing.sm
                    Repeater {
                      model: [ { id: "photos", label: "🖼️ Fotos y videos" }, { id: "document", label: "📄 Documento" },
                               { id: "location", label: "📍 Ubicación" }, { id: "contact", label: "👤 Contacto" },
                               { id: "poll", label: "📊 Encuesta" }, { id: "event", label: "📅 Evento" } ]
                      delegate: Button {
                        required property var modelData
                        text: modelData.label
                        foreground: root.fg; fontFamily: root.fontFamily; fontSize: Style.font.bodySmall
                        bordered: true
                        enabled: modelData.id !== "event" && !filePicker.running
                        tooltipText: modelData.id === "event" ? "wuzapi todavía no permite enviar eventos" : ""
                        onClicked: root.openAttach(modelData.id)
                      }
                    }
                  }

                  // files
                  Text {
                    visible: root.attachMode === "media"
                    textFormat: Text.PlainText
                    text: root.attachFiles.map(function(f) { return "• " + f.split("/").pop() }).join("\n")
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    elide: Text.ElideMiddle
                    Layout.fillWidth: true
                  }
                  TextField {
                    id: captionField
                    visible: root.attachMode === "media"
                    Layout.fillWidth: true
                    placeholderText: root.attachFiles.length > 1 ? "Comentario (va con el primero)" : "Comentario (opcional)"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  // location
                  TextField {
                    id: locField
                    visible: root.attachMode === "location"
                    Layout.fillWidth: true
                    placeholderText: "Coordenadas (-33.45, -70.66) o link de Google Maps"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }
                  TextField {
                    id: locName
                    visible: root.attachMode === "location"
                    Layout.fillWidth: true
                    placeholderText: "Nombre del lugar (opcional)"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  // contact
                  TextField {
                    visible: root.attachMode === "contact"
                    Layout.fillWidth: true
                    placeholderText: "Buscar en la agenda de " + root.curLabel
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    onTextChanged: root.attachSearch = text
                  }
                  ListView {
                    visible: root.attachMode === "contact"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, 200)
                    clip: true
                    model: root.attachContacts
                    delegate: Rectangle {
                      id: pickRow
                      required property var modelData
                      width: ListView.view.width
                      height: pickText.implicitHeight + Style.spacing.sm * 2
                      radius: Style.cornerRadius
                      color: pickMouse.containsMouse ? Style.hoverFillFor(root.fg, root.accent) : "transparent"
                      Text {
                        id: pickText
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left; anchors.leftMargin: Style.spacing.md
                        textFormat: Text.PlainText
                        text: pickRow.modelData.name + "   +" + pickRow.modelData.phone
                        color: root.fg
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.bodySmall
                      }
                      MouseArea {
                        id: pickMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: !root.sending
                        onClicked: root.runAttach([["--kind=contact", "--name=" + pickRow.modelData.name,
                                                    "--phone=" + pickRow.modelData.phone]], false)
                      }
                    }
                  }

                  // poll
                  TextField {
                    id: pollQuestion
                    visible: root.attachMode === "poll"
                    Layout.fillWidth: true
                    placeholderText: "Pregunta"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }
                  Repeater {
                    model: pollOptions
                    delegate: TextField {
                      required property int index
                      required property string v
                      visible: root.attachMode === "poll"
                      Layout.fillWidth: true
                      placeholderText: "Opción " + (index + 1)
                      text: v
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      onTextChanged: pollOptions.setProperty(index, "v", text)
                    }
                  }
                  Button {
                    visible: root.attachMode === "poll" && pollOptions.count < 12
                    text: "+ Opción"
                    foreground: root.fg; fontFamily: root.fontFamily; fontSize: Style.font.bodySmall
                    bordered: true
                    onClicked: pollOptions.append({ v: "" })
                  }

                  Button {
                    visible: ["media", "location", "poll"].indexOf(root.attachMode) !== -1
                    text: root.sending ? "Enviando…" : "Enviar"
                    foreground: root.fg; fontFamily: root.fontFamily
                    bordered: true
                    enabled: !root.sending
                    onClicked: {
                      var jobs = []
                      if (root.attachMode === "media") {
                        for (var i = 0; i < root.attachFiles.length; i++)
                          jobs.push(["--kind=media", "--file=" + root.attachFiles[i]]
                                    .concat(i === 0 && captionField.text.trim() !== "" ? ["--caption=" + captionField.text] : []))
                      } else if (root.attachMode === "location") {
                        var ll = root.parseLatLon(locField.text)
                        if (!ll) { root.status = "No encuentro coordenadas en eso."; root.statusWarn = true; return }
                        jobs.push(["--kind=location", "--lat=" + ll[0], "--lon=" + ll[1], "--name=" + locName.text.trim()])
                      } else {
                        var job = ["--kind=poll", "--question=" + pollQuestion.text]
                        for (var k = 0; k < pollOptions.count; k++)
                          if (pollOptions.get(k).v.trim() !== "") job.push("--option=" + pollOptions.get(k).v)
                        jobs.push(job)
                      }
                      root.runAttach(jobs, false)
                    }
                  }
                }
              }

              // -- @mention suggestions --
              Rectangle {
                visible: root.mentionMatches.length > 0
                Layout.fillWidth: true
                implicitHeight: mentionCol.implicitHeight + Style.spacing.sm * 2
                color: root.bg
                border.width: Style.spacing.hairline
                border.color: Style.normalBorderFor(root.fg, root.accent)
                radius: Style.cornerRadius
                Column {
                  id: mentionCol
                  anchors.fill: parent
                  anchors.margins: Style.spacing.sm
                  Repeater {
                    model: root.mentionMatches
                    delegate: Rectangle {
                      id: mentionRow
                      required property var modelData
                      required property int index
                      width: mentionCol.width
                      height: mentionText.implicitHeight + Style.spacing.sm * 2
                      radius: Style.cornerRadius
                      color: mentionRow.index === root.mentionIndex ? Style.selectedFillFor(root.fg, root.accent)
                             : (mentionMouse.containsMouse ? Style.hoverFillFor(root.fg, root.accent) : "transparent")
                      Text {
                        id: mentionText
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left; anchors.leftMargin: Style.spacing.md
                        textFormat: Text.PlainText
                        text: "@" + mentionRow.modelData.name
                        color: root.fg
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                      }
                      MouseArea {
                        id: mentionMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: root.pickMention(mentionRow.modelData)
                      }
                    }
                  }
                }
              }

              // -- composer --
              Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(Math.max(composer.implicitHeight, 40), 140)
                color: Style.normalFillFor(root.fg, root.accent)
                border.width: Style.spacing.hairline
                border.color: composer.activeFocus ? Style.focusBorderFor(root.fg, root.accent)
                                                     : Style.normalBorderFor(root.fg, root.accent)
                radius: Style.cornerRadius
                ScrollView {
                  anchors.fill: parent
                  clip: true
                  ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                  TextArea {
                    id: composer
                    placeholderText: "Escribí un mensaje · Enter envía · Ctrl+Enter redacta con Claude · Shift+Enter salto"
                    color: root.fg
                    placeholderTextColor: Color.muted
                    wrapMode: TextEdit.Wrap
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    selectByMouse: true
                    readOnly: root.sending || root.drafting
                    background: null
                    onTextChanged: root.updateMention()
                    onCursorPositionChanged: root.updateMention()
                    Keys.onPressed: function(event) {
                      var n = root.mentionMatches.length
                      if (n > 0) {
                        if (event.key === Qt.Key_Down) { root.mentionIndex = (root.mentionIndex + 1) % n; event.accepted = true; return }
                        if (event.key === Qt.Key_Up) { root.mentionIndex = (root.mentionIndex + n - 1) % n; event.accepted = true; return }
                        if (event.key === Qt.Key_Escape) { root.mentionOpen = false; event.accepted = true; return }
                        if (event.key === Qt.Key_Tab || ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                                         && !(event.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)))) {
                          root.pickMention(root.mentionMatches[Math.min(root.mentionIndex, n - 1)])
                          event.accepted = true
                          return
                        }
                      }
                      if (event.key !== Qt.Key_Return && event.key !== Qt.Key_Enter) return
                      if (event.modifiers & Qt.ShiftModifier) return  // newline
                      event.accepted = true
                      if (event.modifiers & Qt.ControlModifier) root.requestDraft()
                      else root.doSend(composer.text, false)
                    }
                  }
                }
              }

              RowLayout {
                Layout.fillWidth: true
                spacing: Style.spacing.md
                Button {
                  text: "📎 Adjuntar"
                  foreground: root.fg
                  fontFamily: root.fontFamily
                  fontSize: Style.font.bodySmall
                  bordered: true
                  selected: root.attachMode !== ""
                  enabled: !root.sending && !root.drafting
                  onClicked: root.attachMode = root.attachMode === "" ? "menu" : ""
                }
                Text {
                  text: "Sale por: " + root.curLabel
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  text: root.status
                  color: root.statusWarn ? root.urgent : Color.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                  Layout.fillWidth: true
                }
                Button {
                  visible: root.failedText !== ""
                  text: "Reintentar"
                  foreground: root.fg
                  fontFamily: root.fontFamily
                  bordered: true
                  enabled: !root.sending
                  onClicked: root.doSend(root.failedText, false)
                }
              }
            }
          }
        }
      }
    }
  }
}
