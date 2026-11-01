"""平台原始昵称与群名片；不把宿主合并后的显示名当成 QQ 昵称。"""


def event_names(event):
    raw = getattr(event.message_obj, "raw_message", None)
    sender = (
        raw.get("sender") if isinstance(raw, dict) else getattr(raw, "sender", None)
    )
    if not isinstance(sender, dict):
        return None, None

    def value(key):
        item = sender.get(key)
        return item.strip() if isinstance(item, str) else None

    return value("nickname"), value("card")
