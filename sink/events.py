"""Turn a wuzapi webhook event into a queue entry, or decide to drop it.

The chat JID is copied verbatim from Info.Chat. It is never rebuilt from a
phone number: wuzapi resolves a bare id through a PN/LID lookup that can be
ambiguous, and getting it wrong means messaging the wrong person.
"""

PLACEHOLDERS = {
    "audioMessage": "[audio]",
    "imageMessage": "[imagen]",
    "videoMessage": "[video]",
    "stickerMessage": "[sticker]",
    "documentMessage": "[documento]",
    "locationMessage": "[ubicación]",
    "contactMessage": "[contacto]",
}

# Message keys wuzapi can actually fetch, mapped to the short type name
# stored on the queue entry. Checked against wuzapi's own source
# (routes.go / handlers.go): /chat/downloadimage, /chat/downloadaudio,
# /chat/downloadvideo, /chat/downloaddocument and /chat/downloadsticker
# each exist and decode a JSON body into a struct with these exact fields
# (Url, DirectPath, MediaKey, Mimetype, FileEncSHA256, FileSHA256,
# FileLength). There is no such endpoint for locationMessage or
# contactMessage — those can never be downloadable, endpoint or no.
DOWNLOADABLE_TYPES = {
    "audioMessage": "audio",
    "imageMessage": "image",
    "videoMessage": "video",
    "stickerMessage": "sticker",
    "documentMessage": "document",
}

# The exact keys wuzapi's download endpoints require, spelled the way
# whatsmeow's own proto JSON emits them on the incoming event (verified
# against waE2E's generated struct tags, e.g. ImageMessage.URL has
# `json:"URL,omitempty"`, not "url"). Go's json.Decoder matches struct
# field names case-insensitively, so handing these keys back untouched is
# exactly what the endpoint's downloadImageStruct/downloadAudioStruct/...
# expect — no renaming needed, and no risk of guessing a shape that
# doesn't match what wuzapi actually reads.
DOWNLOAD_FIELDS = ("URL", "mimetype", "mediaKey", "fileSHA256",
                   "fileEncSHA256", "fileLength", "directPath")


# Events WhatsApp delivers as messages but the phone never shows as one:
# reactions, key exchange, edits/deletes, pins. Queueing them left entries
# with no sender name and no text that no read receipt could ever clear,
# because there is nothing unread on the phone to read.
INVISIBLE = {"reactionMessage", "encReactionMessage", "senderKeyDistributionMessage",
             "protocolMessage", "pinInChatMessage", "keepInChatMessage",
             "pollUpdateMessage", "messageContextInfo"}

# Wrappers whose real message sits one level down under "message".
WRAPPERS = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
            "viewOnceMessageV2Extension", "documentWithCaptionMessage")


def _unwrap(message):
    for _ in range(3):  # ponytail: wrappers nest at most twice in practice
        if not isinstance(message, dict):
            return message
        for key in WRAPPERS:
            inner = message.get(key)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                message = inner["message"]
                break
        else:
            return message
    return message


def _is_invisible(message) -> bool:
    return isinstance(message, dict) and bool(message) and set(message) <= INVISIBLE


def _text_of(message: dict) -> str:
    if not isinstance(message, dict):
        return "[mensaje no soportado]"
    # An empty `conversation` is not text: treating it as one produced a
    # queue entry with a blank body that the user could not act on.
    if isinstance(message.get("conversation"), str) and message["conversation"]:
        return message["conversation"]
    ext = message.get("extendedTextMessage")
    if isinstance(ext, dict) and isinstance(ext.get("text"), str) and ext["text"]:
        return ext["text"]
    for key, label in PLACEHOLDERS.items():
        if key in message:
            return label
    return "[mensaje no soportado]"


def _attachment_of(message: dict) -> dict | None:
    """What `wa-media` will need to fetch this message's attachment, if any.

    Returns None for a plain text message (no attachment key present at
    all — a text message must not gain any of these fields). For a
    recognised attachment type, returns a dict with "type" and
    "downloadable"; when downloadable it also carries the exact fields
    wuzapi's matching /chat/download* endpoint requires, copied verbatim
    from the event. A type wuzapi has no download endpoint for
    (locationMessage, contactMessage) is always marked not downloadable.
    A downloadable type missing one or more required fields (a malformed
    event) is also marked not downloadable, with none of the partial
    fields kept — a caller must not be able to fetch with half a key.
    """
    if not isinstance(message, dict):
        return None
    for key, label in DOWNLOADABLE_TYPES.items():
        if key in message:
            payload = message[key] if isinstance(message[key], dict) else {}
            fields = {f: payload[f] for f in DOWNLOAD_FIELDS
                     if f in payload and payload[f] is not None}
            if len(fields) == len(DOWNLOAD_FIELDS):
                return {"type": label, "downloadable": True, **fields}
            return {"type": label, "downloadable": False}
    for key in PLACEHOLDERS:
        if key in message and key not in DOWNLOADABLE_TYPES:
            return {"type": PLACEHOLDERS[key].strip("[]"), "downloadable": False}
    return None


def extract(event: dict, groups_enabled) -> dict | None:
    info = (event or {}).get("event", {}).get("Info", {})
    if not info:
        return None
    if info.get("IsFromMe"):
        return None

    jid = info.get("Chat", "")
    if not jid:
        return None

    if info.get("IsGroup") and jid not in (groups_enabled or []):
        return None

    message = _unwrap((event or {}).get("event", {}).get("Message", {}))
    if _is_invisible(message):
        return None
    entry = {
        "jid": jid,
        "push_name": info.get("PushName") or jid,
        "text": _text_of(message),
        "ts": info.get("Timestamp", ""),
    }
    attachment = _attachment_of(message)
    if attachment is not None:
        entry["attachment"] = attachment
    return entry


def extract_own_message(event: dict) -> dict | None:
    """The ring-entry ingredients for a message the owner sent from ANY
    device (`IsFromMe` true), or None if this event isn't one of those.

    Unlike `extract`, this never looks at `groups_enabled` — a message the
    owner sent is never something to queue as incoming, so the group
    allow-list (which exists to keep unwanted group chatter out of the
    queue) has nothing to say about it here.
    """
    info = (event or {}).get("event", {}).get("Info", {})
    if not isinstance(info, dict) or not info.get("IsFromMe"):
        return None
    jid = info.get("Chat", "")
    if not jid:
        return None
    message = _unwrap((event or {}).get("event", {}).get("Message", {}))
    if _is_invisible(message):
        return None
    return {"jid": jid, "text": _text_of(message), "ts": info.get("Timestamp", "")}


def extract_read_receipt(event: dict) -> dict | None:
    """The chat jid a `ReadReceipt` says the OWNER has now read, or None.

    A read receipt travels in both directions and wuzapi tells them apart
    with its own `state` field, set from whatsmeow's ReceiptType:

    - "ReadSelf" — one of the owner's OTHER devices marked an incoming
      chat as read. This is "the owner is caught up on it" — the signal
      this function exists to recognise.
    - "Read" — the *other* party read a message the owner sent. The
      opposite direction; must never be treated as "handled" (see the
      design doc's "la trampa").
    - "Delivered" (or anything else) — no read information at all.

    The direction is read from this one field, never inferred from
    `IsFromMe`/`IsGroup` on the receipt itself: those describe whose chat
    the receipt is about, not which side did the reading.
    """
    if not event or event.get("state") != "ReadSelf":
        return None
    receipt = event.get("event")
    if not isinstance(receipt, dict):
        return None
    jid = receipt.get("Chat", "")
    if not jid:
        return None
    return {"jid": jid}


def describe_skipped_group(event: dict, groups_enabled) -> dict | None:
    """Identify a group message dropped only because its JID isn't enabled.

    Separate from `extract` on purpose: `extract` returns `None` for every
    kind of discard (own message, no info, group not enabled, ...) and
    callers that only see that `None` cannot tell them apart. This gives
    the sink a way to answer "what is the JID of that group" without
    changing `extract`'s contract or its tests.
    """
    info = (event or {}).get("event", {}).get("Info", {})
    if not info or info.get("IsFromMe"):
        return None

    jid = info.get("Chat", "")
    if not jid or not info.get("IsGroup"):
        return None

    if jid in (groups_enabled or []):
        return None

    # PushName is the display name of whoever posted this particular
    # message (same field extract() reads as push_name for the sender),
    # not the group's subject/title — there is no group-subject field
    # here to read. Key it "sender" so callers don't mistake it for one.
    return {"jid": jid, "sender": info.get("PushName") or jid}
