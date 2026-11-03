"""心弦好感度 - 聊天指令 handler。

由 main.py 中 @filter.command 装饰的 Star 方法薄壳调用。
自身关系查询所有成员可用；排行统一检查可配置权限；设置/重置仍须宿主管理权限。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..core.decimal import fmt
from ..core.scope import is_account_id
from ..services.favor_service import FavorService
from . import llm_tools


async def handle_ranking(
    svc: FavorService,
    event: AstrMessageEvent,
    limit: int = 10,
    *,
    ranking_access: str = "all",
) -> str:
    """/好感排行 —— 查本群榜单。"""
    return await llm_tools.tool_query_ranking(
        svc, event, limit, ranking_access=ranking_access
    )


async def handle_set(
    svc: FavorService, event: AstrMessageEvent, target: str, value: float
) -> str:
    """/好感设置 <QQ号> <数值>（管理员，数值支持一位小数与负值）。"""
    target = (target or "").strip()
    if not is_account_id(target):
        return "用法：/好感设置 QQ号 数值"
    rec = await svc.set_favor(event.get_group_id(), target, value)
    space = "此 Bot 的全局空间" if svc.scope_mode == "global" else "本群"
    return f"已将 QQ {target} 在{space}的好感度设置为 {fmt(rec.favor)}。"


async def handle_reset(
    svc: FavorService, event: AstrMessageEvent, target: str = ""
) -> str:
    """/好感重置 [QQ号]（管理员）。不带参数清空整群。"""
    group_id = event.get_group_id()
    target = (target or "").strip()
    if target:
        if not is_account_id(target):
            return "用法：/好感重置 [QQ号]"
        await svc.reset(group_id, target)
        space = "此 Bot 的全局空间" if svc.scope_mode == "global" else "本群"
        return f"已重置 QQ {target} 在{space}的好感度。"
    if svc.scope_mode == "global":
        return "当前为全局关系空间，群内不支持清空全部共享关系；请在管理面板选择明确的数据空间。"
    await svc.reset(group_id, None)
    return "已重置本群全部好感度数据。"


async def handle_set_relationship(
    svc: FavorService, relationships, event: AstrMessageEvent, target: str, key: str
) -> str:
    """/关系设置 QQ号 类型（管理员；类型留空=清除）。"""
    target = (target or "").strip()
    key = (key or "").strip()
    if not is_account_id(target):
        keys = "、".join(relationships.keys()) if relationships else "任意自定义"
        return f"用法：/关系设置 QQ号 类型（可选：{keys}；留空清除）"
    await svc.set_relationship(event.get_group_id(), target, key)
    if not key:
        return f"已清除 QQ {target} 与当前 Bot的关系标签。"
    label = relationships.label_of(key) if relationships else key
    return f"已将 QQ {target} 与当前 Bot的关系设为「{label}」。"


async def handle_set_tags(
    impressions, event: AstrMessageEvent, target: str, tags: str
) -> str:
    """/印象设置 QQ号 标签1,标签2（管理员；标签留空=清除手动标签）。印象本体由 AI 维护。"""
    target = (target or "").strip()
    if not is_account_id(target):
        return "用法：/印象设置 QQ号 标签1,标签2（最多 3 个，逗号分隔；留空清除）"
    parts = [p for p in str(tags or "").replace("，", ",").split(",") if p.strip()]
    if not parts:
        await impressions.set_tags(event.get_group_id(), target, [])
        return f"已清除 QQ {target} 的标签。"
    await impressions.set_tags(event.get_group_id(), target, parts[:3])
    return (
        f"已将 QQ {target} 的标签设为：{'、'.join(p.strip()[:6] for p in parts[:3])}。"
    )


async def handle_refresh_impression(
    impressions, event: AstrMessageEvent, target: str
) -> str:
    """/印象刷新 QQ号（管理员）：立即根据最近评估记录刷新该成员印象。"""
    target = (target or "").strip()
    if not is_account_id(target):
        return "用法：/印象刷新 QQ号"
    _ok, msg = await impressions.refresh_now(
        event.get_group_id(),
        target,
        umo=getattr(event, "unified_msg_origin", "") or "",
    )
    return msg


async def rank_reply(
    svc: FavorService,
    event: AstrMessageEvent,
    *,
    font_path: str = "",
    rows_per_col: int = 12,
    text_limit: int = 10,
    ranking_access: str = "all",
    image_enabled: bool = True,
    avatar_service=None,
) -> tuple[str, str]:
    """权限检查后查询一次；图片只接收公开资料，绘制失败回退同一份文字。"""
    reason = llm_tools.ranking_denial(svc, event, ranking_access)
    if reason:
        return "text", reason
    rows, self_rank, avatar_ids = await llm_tools.ranking_rows(svc, event, text_limit)
    text = llm_tools.ranking_text(rows)
    if image_enabled and rows:
        import asyncio
        from astrbot.api import logger

        try:
            from .rank_image import render_ranking

            avatars = await avatar_service.load(avatar_ids) if avatar_service else []
            path = await asyncio.to_thread(
                render_ranking, rows, self_rank, font_path=font_path, avatars=avatars
            )
            return "image", path
        except Exception:
            logger.warning("[Bond] 排行图片生成失败，已回退文字")
    return "text", text


async def try_text_wake(
    svc: FavorService,
    event: AstrMessageEvent,
    ranking_phrases: set[str],
    font_path: str = "",
    rows_per_col: int = 12,
    text_limit: int = 10,
    *,
    ranking_access: str = "all",
    image_enabled: bool = True,
    avatar_service=None,
) -> tuple[str, str] | None:
    """精确短语与命令共用权限、公开资料和图片失败降级。"""
    msg = (event.message_str or "").strip()
    if not msg or msg.startswith("/"):
        return None
    if msg in ranking_phrases:
        return await rank_reply(
            svc,
            event,
            font_path=font_path,
            rows_per_col=rows_per_col,
            text_limit=text_limit,
            ranking_access=ranking_access,
            image_enabled=image_enabled,
            avatar_service=avatar_service,
        )
    return None
