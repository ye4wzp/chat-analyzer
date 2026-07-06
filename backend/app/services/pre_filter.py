import re

from app.core.config import Config, load_config


def is_noise(
    content: str,
    msg_type: str,
    sender_name: str | None = None,
    cfg: Config | None = None,
    vip_extra: frozenset[str] | set[str] = frozenset(),
    sender_id: str | None = None,
) -> bool:
    """Check if a message is noise that should be pre-filtered.

    VIP contacts bypass the filter entirely. `vip_extra` carries alias-resolved
    identities (platform names/ids from contact_aliases) so VIP status survives
    display-name changes.
    """
    if cfg is None:
        cfg = load_config()

    if sender_name and (sender_name in cfg.vip_contacts or sender_name in vip_extra):
        return False
    if sender_id and str(sender_id) in vip_extra:
        return False

    if msg_type in cfg.pre_filter.skip_msg_types:
        return True

    if len(content.strip()) < cfg.pre_filter.min_content_length:
        return True

    for pattern in cfg.pre_filter.skip_content:
        if re.match(pattern, content.strip()):
            return True

    return False


def filter_messages(
    messages: list[dict],
    cfg: Config | None = None,
    vip_extra: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[dict], list[dict]]:
    """Split messages into (passed, filtered_as_noise). Config is loaded once
    per batch, not per message."""
    if cfg is None:
        cfg = load_config()
    passed = []
    noise = []
    for msg in messages:
        if is_noise(
            msg.get("content", ""),
            msg.get("msg_type", "text"),
            msg.get("sender_name"),
            cfg,
            vip_extra,
            msg.get("sender_id"),
        ):
            noise.append(msg)
        else:
            passed.append(msg)

    return passed, noise
