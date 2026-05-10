import re

from app.core.config import load_config


def is_noise(content: str, msg_type: str, sender_name: str | None = None) -> bool:
    """Check if a message is noise that should be pre-filtered.

    VIP contacts bypass the filter entirely.
    """
    cfg = load_config()

    # VIP contacts skip pre-filter
    if sender_name and sender_name in cfg.vip_contacts:
        return False

    # Skip by message type
    if msg_type in cfg.pre_filter.skip_msg_types:
        return True

    # Skip too short
    if len(content.strip()) < cfg.pre_filter.min_content_length:
        return True

    # Skip by content patterns
    for pattern in cfg.pre_filter.skip_content:
        if re.match(pattern, content.strip()):
            return True

    return False


def filter_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split messages into (passed, filtered_as_noise).

    Filtered messages get category='casual' added.
    """
    passed = []
    noise = []
    for msg in messages:
        content = msg.get("content", "")
        msg_type = msg.get("msg_type", "text")
        sender = msg.get("sender_name")

        if is_noise(content, msg_type, sender):
            msg["category"] = "casual"
            noise.append(msg)
        else:
            passed.append(msg)

    return passed, noise
