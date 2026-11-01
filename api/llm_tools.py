"""心弦好感度 - LLM 工具 handler。

由 main.py 中 @filter.llm_tool 装饰的 Star 方法薄壳调用。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..services.favor_service import FavorService


async def tool_query_favor(
    svc: FavorService, event: AstrMessageEvent, target: str = ""
) -> str:
    """普通查询只返回当前发言者的关系称谓。"""
    group_id, sender = event.get_group_id(), str(event.get_sender_id())
    if not group_id:
        return "请在群聊中查询自己的关系。"
    if target and str(target).strip() != sender:
        return "只能查询你自己的关系称谓。"
    return "你与当前 Bot 的关系：" + await svc.relation_title(group_id, sender) + "。"


async def tool_query_ranking(
    svc: FavorService, event: AstrMessageEvent, limit: int = 10
) -> str:
    """本群按亲近度排序，最多十人，只公开称谓。"""
    group_id = event.get_group_id()
    if not group_id:
        return "请在群聊中查询本群排行。"
    rows = await svc.ranking(group_id, min(10, max(1, int(limit))))
    if not rows:
        return "本群还没有关系记录。"
    lines = []
    for i, record in enumerate(rows, 1):
        label = " ".join((record.nickname or record.user_id).split())[:40]
        title = await svc.relation_title(group_id, record.user_id)
        lines.append(f"{i}. {label}：{title}")
    return "本群关系排行（按亲近度排序）：\n" + "\n".join(lines)
