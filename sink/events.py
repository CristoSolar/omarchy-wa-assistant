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


def _text_of(message: dict) -> str:
    if not isinstance(message, dict):
        return "[mensaje no soportado]"
    if isinstance(message.get("conversation"), str):
        return message["conversation"]
    ext = message.get("extendedTextMessage")
    if isinstance(ext, dict) and isinstance(ext.get("text"), str):
        return ext["text"]
    for key, label in PLACEHOLDERS.items():
        if key in message:
            return label
    return "[mensaje no soportado]"


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

    message = (event or {}).get("event", {}).get("Message", {})
    return {
        "jid": jid,
        "push_name": info.get("PushName") or jid,
        "text": _text_of(message),
        "ts": info.get("Timestamp", ""),
    }
