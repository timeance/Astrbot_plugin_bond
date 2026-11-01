"""从宿主 OneBot 反向连接发现真实账号，不使用平台配置 ID。"""

from __future__ import annotations
from ..core.scope import is_account_id


class BotDiscovery:
    def __init__(self, context):
        self.context = context

    def account_ids(self) -> set[str]:
        manager = getattr(self.context, "platform_manager", None)
        platforms = getattr(manager, "platform_insts", ())
        ids = set()
        for platform in list(platforms):
            try:
                if platform.meta().name != "aiocqhttp":
                    continue
                client = platform.get_client()
                # AstrBot 4.28.0 的 CQHttp 在此按 X-Self-ID 保存当前 API 连接；
                # 一个适配器可同时连接多个真实账号。'*' 路由不能作为账号。
                connections = getattr(client, "_wsr_api_clients", None)
                if not isinstance(connections, dict):
                    continue
                ids.update(key for key in list(connections) if is_account_id(key))
            except Exception:
                # 适配器正在启动或卸载时没有可信账号，保留本地已知记录。
                continue
        return ids
