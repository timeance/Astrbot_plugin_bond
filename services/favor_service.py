"""心弦好感度 - 好感度应用服务。

增减、查询、防刷（单事件冷却 + 每日双向限幅）。
所有变化都经这里收口，是好感度数值管理的唯一入口。
成员印象的维护在 ImpressionService（v1.29.3 拆出）。
"""

from __future__ import annotations

import time
import math
from datetime import date, datetime

from ..core.decay import HALF_LIFE_MIN, effective_favor
from ..core.decimal import round1
from ..core.identity import is_master as _is_master
from ..core.levels import LevelTable
from ..core.level_economy import EconomyConfig, apply as apply_economy
from ..core.level_economy import repair_params_for_level
from ..core.relationship import RelationshipTable
from ..core.models import FavorChange, FavorRecord, LevelDef
from ..storage.base import StorageBackend
from ..core.scope import group_key, scope_mode as validate_scope


class FavorService:
    """好感度门面服务。"""

    def __init__(
        self,
        storage: StorageBackend,
        levels: LevelTable,
        *,
        max_favor: float = 100,
        min_favor: float = -100,
        default_favor: float = 0,
        daily_cap_up: float = 4,
        daily_cap_down: float = 8,
        master_ids: list[str] | tuple[str, ...] = (),
        decay_enabled: bool = False,
        half_life_base: float = 10.0,
        half_life_growth: float = 1.3,
        half_life_max: float = 60.0,
        decay_baseline: float = 0.0,
        relationships: RelationshipTable | None = None,
        economy: EconomyConfig | None = None,
        tz_name: str = "",
        decay_floor_enabled: bool = False,
        scope_mode: str = "per_group",
        relation_policy=None,
        relation_resolver=None,
        relation_caps=None,
    ) -> None:
        from ..core.relation_policy import RelationPolicy, RelationResolver

        self._relation_policy = relation_policy or RelationPolicy()
        self._relation_resolver = relation_resolver or RelationResolver()
        self._relation_caps = relation_caps or dict(
            familiarity=(2, 0), affinity=(3, 6), trust=(1, 4)
        )
        if set(self._relation_caps) != {"familiarity", "affinity", "trust"}:
            raise ValueError("三维每日限幅配置不完整")
        for pair in self._relation_caps.values():
            if len(pair) != 2 or any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                or x < 0
                for x in pair
            ):
                raise ValueError("每日限幅必须是有限非负数")
        self._storage = storage
        self.scope_mode = validate_scope(scope_mode)
        self._levels = levels
        self.max_favor = max_favor
        self.min_favor = min_favor
        self.default_favor = default_favor
        # 等级衰减地板（星露谷式）：开启后按"最多跌两级"托底——衰减读值
        # 不低于当前等级往下两级的下沿（挚爱最多衰到亲密档），久别重逢
        # 不掉出熟悉区间。地板只对存量高于地板的记录生效。
        self._decay_floor_enabled = decay_floor_enabled
        # 配置钳制：负上限 / <1 的巩固系数会让限幅与遗忘方向反着来，这里兜住
        self.daily_cap_up = max(0.0, float(daily_cap_up))
        self.daily_cap_down = max(0.0, float(daily_cap_down))
        self._master_ids = list(master_ids)
        # 指数遗忘衰减参数：(半衰期基准, 巩固增长系数, 半衰期上限, 基线)。
        # 基准/上限不低于 HALF_LIFE_MIN 保险丝，巩固系数不小于 1（否则正互动
        # 反而缩短半衰期）；上限不小于基准。
        base = max(HALF_LIFE_MIN, float(half_life_base))
        growth = max(1.0, float(half_life_growth))
        h_max = max(base, float(half_life_max))
        self._decay = (
            (base, growth, h_max, float(decay_baseline)) if decay_enabled else None
        )
        self._storage.configure_initial_half_life(base if decay_enabled else 10.0)
        self._relationships = relationships
        self._economy = economy
        self._nick_cache: dict[tuple[str, str], str] = {}
        # 每日限幅/同日衰减的"一天"边界时区。默认东八区（插件面向 QQ/中文
        # 社区，而 Docker 容器系统时区多为 UTC——按 UTC 换日会让"每天"在
        # 北京时间早 8 点才开始）；显式配置可覆盖为任意 IANA 时区名。
        self._tz = None
        try:
            import zoneinfo

            self._tz = zoneinfo.ZoneInfo((tz_name or "").strip() or "Asia/Shanghai")
        except Exception:
            self._tz = None

    # ---------- 查询 ----------

    def _today(self, now: float | None = None) -> date:
        """配置时区下的"今天"（每日限幅/同日衰减的边界）。"""
        t = time.time() if now is None else now
        dt = (
            datetime.fromtimestamp(t, self._tz)
            if self._tz
            else datetime.fromtimestamp(t)
        )
        return dt.date()

    def _day_start(self, now: float | None = None) -> float:
        """配置时区下今天 0 点的 epoch 秒。"""
        d = self._today(now)
        dt = (
            datetime(d.year, d.month, d.day, tzinfo=self._tz)
            if self._tz
            else datetime(d.year, d.month, d.day)
        )
        return dt.timestamp()

    def _decay_floor(self, stored: float, baseline: float) -> float | None:
        """按"最多跌两级"算衰减地板：当前等级往下两级的下沿。

        等级表升序（厌恶→挚爱），当前等级 index i 的地板 = 等级 i-2 的
        min_score（挚爱 95 → 地板=亲密下沿 55）——久别重逢最多淡两档，
        仍是"认识的老朋友"而非"你是哪位"。最低两级（厌恶/陌生）无地板
        （向基线衰减无所谓）。地板必须高于 baseline 才有意义，否则 None。
        """
        if not self._decay_floor_enabled:
            return None
        levels = self._levels.all()
        idx = next(
            (i for i, lv in enumerate(levels) if lv is self._levels.level_of(stored)), 0
        )
        if idx < 2:
            return None
        floor = levels[idx - 2].min_score
        return floor if floor > baseline else None

    def _effective(
        self, stored: float, updated_at: float, half_life: float = 10.0
    ) -> float:
        """读取时的有效好感度（启用衰减时按指数遗忘曲线向基线收敛）。

        half_life 取自该成员记录（正互动巩固过的老朋友衰减更慢）；
        启用地板时按"最多跌两级"托底。
        """
        if self._decay is None:
            return round1(stored)
        base, _growth, _h_max, baseline = self._decay
        return effective_favor(
            stored,
            updated_at,
            time.time(),
            half_life=(half_life if half_life and half_life > 0 else base),
            baseline=baseline,
            floor=self._decay_floor(stored, baseline),
        )

    async def get(self, group_id: str, user_id: str) -> FavorRecord:
        """读取记录；无记录时返回默认值（不落库）。返回的是有效好感度（含时间衰减）。"""
        group_id = self.group_key(group_id)
        rec = await self._storage.get(group_id, user_id)
        if rec is None:
            rec = FavorRecord(
                group_id=group_id,
                user_id=user_id,
                favor=self.default_favor,
                updated_at=0.0,
            )
        else:
            rec.favor = self._effective(rec.favor, rec.updated_at, rec.half_life)
        return rec

    async def get_relation(self, group_id, user_id):
        from ..core.relation_policy import RelationState

        rec = await self.get(group_id, user_id)
        return RelationState(
            familiarity=rec.familiarity, affinity=rec.favor, trust=rec.trust
        )

    async def relation_title(self, group_id, user_id):
        from ..core.relation_policy import RelationState

        rec = await self.get(group_id, user_id)
        # 旧标量范围可超出新维度边界；只在称谓分类时饱和，不改原始数值。
        return self._relation_resolver.resolve(
            RelationState(
                familiarity=rec.familiarity,
                affinity=max(-100, min(100, rec.favor)),
                trust=rec.trust,
            )
        )

    @staticmethod
    def _validate_event_id(event_id):
        if (
            not isinstance(event_id, str)
            or not event_id
            or len(event_id) > 200
            or any(ord(c) < 32 for c in event_id)
        ):
            raise ValueError("需要可信事件的稳定 ID")

    async def apply_relation_event(
        self,
        group_id,
        user_id,
        event_type,
        *,
        event_id,
        intensity=1,
        reason="关系事件",
        message="",
        cooldown_sec=120,
    ):
        """供可信评估服务调用；身份与事件分类在平台边界核验后传入。"""
        self._validate_event_id(event_id)
        if (
            isinstance(cooldown_sec, bool)
            or not isinstance(cooldown_sec, (int, float))
            or not math.isfinite(cooldown_sec)
            or cooldown_sec < 0
        ):
            raise ValueError("冷却时间无效")
        deltas = self._relation_policy.deltas(event_type, intensity)
        # 沿用上游经济规则，仅作用于亲近度；另两维独立限幅。
        if self._economy is not None and deltas["affinity"]:
            rec = await self.get(group_id, user_id)
            eco = apply_economy(
                deltas["affinity"],
                self.level_of(rec.favor).name,
                await self._positive_judge_today(group_id, user_id),
                self._economy,
                repair=await self._repair_window(group_id, user_id),
            )
            deltas = self._relation_policy.limit_deltas(
                event_type, dict(deltas, affinity=eco.delta)
            )
        return await self._storage.apply_relation_change(
            self.group_key(group_id),
            user_id,
            deltas,
            event_id=event_id,
            day=self._today().isoformat(),
            caps=self._relation_caps,
            cooldown_sec=cooldown_sec,
            reason=reason,
            message=message,
            effective=self._effective,
            decay=self._decay,
            default_favor=self.default_favor,
        )

    async def set_relation(
        self,
        group_id,
        user_id,
        *,
        familiarity,
        affinity,
        trust,
        event_id,
        reason="管理员调整",
    ):
        """管理入口须先执行宿主权限检查；本服务校验数值并保留审计流水。"""
        from dataclasses import asdict
        from ..core.relation_policy import RelationState

        self._validate_event_id(event_id)
        values = asdict(
            RelationState(familiarity=familiarity, affinity=affinity, trust=trust)
        )
        return await self._storage.apply_relation_change(
            self.group_key(group_id),
            user_id,
            dict.fromkeys(values, 0),
            event_id=event_id,
            day=self._today().isoformat(),
            caps=self._relation_caps,
            source="admin",
            reason=reason,
            values=values,
            effective=self._effective,
            default_favor=self.default_favor,
        )

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        # 衰减后排序可能改变候选集，必须先按有效值重排再取前 N 名。
        if self.scope_mode == "global":
            rows = await self._storage.ranking_observed(group_id, -1)
        else:
            rows = await self._storage.ranking(group_id, -1)
        for r in rows:
            r.favor = self._effective(
                r.favor, r.updated_at, getattr(r, "half_life", 10.0)
            )
        rows.sort(key=lambda r: (-r.favor, r.user_id))
        return rows[:limit] if limit >= 0 else rows

    async def standings(
        self, group_id: str | None = None, limit: int = 500
    ) -> list[dict]:
        """当前总览：每个成员的有效好感/等级/关系/闲置天数（供 WebUI）。按有效好感降序。"""
        if self.scope_mode == "global" and group_id is not None:
            recs = await self._storage.ranking_observed(group_id, -1)
        else:
            recs = await self._storage.list_favor(self.group_key(group_id), -1)
        now = time.time()
        out: list[dict] = []
        for r in recs:
            eff = self._effective(r.favor, r.updated_at, r.half_life)
            idle = int((now - r.updated_at) // 86400) if r.updated_at > 0 else None
            out.append(
                {
                    "group_id": r.group_id,
                    "user_id": r.user_id,
                    "favor": eff,
                    "stored_favor": round1(r.favor),
                    "decayed": round1(r.favor) != eff,
                    "level": self.level_of(eff).name,
                    "relationship": (
                        self.relationship_label(r.relationship)
                        if r.relationship
                        else ""
                    ),
                    "nickname": r.nickname or "",
                    "impression": r.impression or "",
                    "tags": r.parsed_tags(),
                    "impression_at": r.impression_at,
                    "updated_at": r.updated_at,
                    "idle_days": idle,
                }
            )
        out.sort(key=lambda x: x["favor"], reverse=True)
        return out[:limit] if limit >= 0 else out

    def level_of(self, favor: float) -> LevelDef:
        return self._levels.level_of(favor)

    def level_order(self) -> dict[str, int]:
        """等级名 → 升序序位（v1.31.0 情绪耦合用：判定等级跃迁方向/档数）。"""
        return {lv.name: i for i, lv in enumerate(self._levels.all())}

    def is_master(self, user_id: str) -> bool:
        return _is_master(user_id, self._master_ids)

    def relationship_label(self, value: str) -> str:
        """关系值 → 展示名（无关系表或未设置返回原值/空）。"""
        if self._relationships is None:
            return value or ""
        return self._relationships.label_of(value)

    async def set_relationship(
        self, group_id: str, user_id: str, relationship: str
    ) -> None:
        """设定关系类型标签；无记录时先按默认好感建一条再设（不影响好感数值）。"""
        group_id = self.group_key(group_id)
        rec = await self._storage.get(group_id, user_id)
        if rec is None:
            await self._storage.set_value(group_id, user_id, self.default_favor)
        await self._storage.set_relationship(
            group_id, user_id, (relationship or "").strip()
        )

    async def touch_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        """更新成员昵称；带内存缓存，昵称未变不写库（不影响好感数值）。

        无记录时先按 default_favor 建行（否则 INSERT 落列默认 0，
        配置了非零初始好感的新成员会被顶成 0）。"""
        group_id = self.group_key(group_id)
        nick = (nickname or "").strip()
        if not nick:
            return
        key = (group_id, user_id)
        if self._nick_cache.get(key) == nick:
            return
        # 昵称缓存只省一次对比读，全清代价可忽略；封顶防长寿命大群无界增长
        if len(self._nick_cache) >= 4096:
            self._nick_cache.clear()
        if await self._storage.get(group_id, user_id) is None:
            await self._storage.set_value(group_id, user_id, self.default_favor)
        await self._storage.set_nickname(group_id, user_id, nick)
        self._nick_cache[key] = nick

    async def distinct_groups(self) -> list[dict]:
        """有记录的群列表（面板群筛选用；storage 透传，X7 面板不直拿存储）。"""
        if self.scope_mode == "global":
            return await self._storage.observed_groups()
        return await self._storage.distinct_groups()

    def group_key(self, group_id: str | None) -> str | None:
        # per_group 旧服务单测允许合成字母键；平台/API 边界另行严格校验。
        if self.scope_mode == "per_group":
            return group_id
        return group_key(self.scope_mode, group_id)

    async def observe_member(self, group_id: str, user_id: str) -> None:
        await self._storage.observe_member(group_id, user_id)

    async def is_observed(self, group_id: str, user_id: str) -> bool:
        return any(
            row.user_id == user_id
            for row in await self._storage.list_observed(group_id)
        )

    async def query_logs(
        self,
        group_id: str,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
        fuzzy: bool = False,
    ) -> list[dict]:
        """流水查询透传（注入链路一次拉取，milestone/recent_events 共用）。

        fuzzy=True 仅供面板搜索（子串匹配）；内部路径一律精确。"""
        return await self._storage.query_logs(
            self.group_key(group_id), user_id, limit=limit, offset=offset, fuzzy=fuzzy
        )

    async def recent_events(
        self,
        group_id: str,
        user_id: str,
        count: int = 3,
        days: int = 7,
        *,
        sig_threshold: float = 0.0,
        sig_window_mult: float = 1.0,
        logs: list[dict] | None = None,
    ) -> list[dict]:
        """最近 count 条变动流水（倒序），供注入「近期印象」。

        显著性加权（MemoryBank 式）：|delta| ≥ sig_threshold 的重要事件，
        记忆窗口放大 sig_window_mult 倍——大事记得久，小寒暄照常淡忘。
        已撤销（reversed）的变动不作为记忆注入。
        logs: 调用方已查好的流水（注入链路复用同一批，省一次查询）。
        """
        if not count or count <= 0:
            return []
        rows = (
            logs
            if logs is not None
            else await self._storage.query_logs(
                group_id, user_id, limit=max(count * 5, count)
            )
        )
        rows = [r for r in rows if not r.get("reversed")]
        if days and days > 0:
            now = time.time()
            normal_cutoff = now - days * 86400
            sig_cutoff = now - days * 86400 * max(1.0, sig_window_mult)

            def _in_window(r: dict) -> bool:
                if (
                    sig_threshold > 0
                    and abs(float(r.get("delta") or 0)) >= sig_threshold
                ):
                    return r.get("ts", 0) >= sig_cutoff
                return r.get("ts", 0) >= normal_cutoff

            rows = [r for r in rows if _in_window(r)]
        return rows[:count]

    def recent_milestone(
        self,
        group_id: str,
        user_id: str,
        logs: list[dict] | None = None,
        hours: float = 48.0,
    ) -> tuple[str, float] | None:
        """最近 hours 小时内的等级跨越（升级里程碑），返回 (新等级名, ts)。

        数据直接来自流水的 favor_before/favor_after（零额外写入）：等级表
        升序序位抬升即升级。只认评审路径（source="judge"）的有机跨越，
        管理员设置/撤销不算。供注入「关系里程碑」——升级 48h 内小千
        "知道"关系刚升温。已撤销的行不算。logs 需调用方传入（本方法为
        纯同步实现，不查库）。
        """
        try:
            rows = logs or []
            if not rows:
                return None
            order = {lv.name: i for i, lv in enumerate(self._levels.all())}
            best: tuple[str, float] | None = None
            cutoff = time.time() - hours * 3600
            for r in rows:
                if r.get("source") != "judge" or r.get("reversed"):
                    continue
                ts = float(r.get("ts") or 0)
                if ts < cutoff:
                    continue
                i0 = order.get(self.level_of(float(r.get("favor_before") or 0)).name, 0)
                i1 = order.get(self.level_of(float(r.get("favor_after") or 0)).name, 0)
                if i1 > i0 and (best is None or ts > best[1]):
                    best = (self._levels.all()[i1].name, ts)
            return best
        except Exception:
            return None

    # ---------- 增减 ----------

    async def apply_judge(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        reason: str = "judge",
        *,
        message: str = "",
    ) -> FavorChange:
        """应用 LLM 评估结果（judge 的冷却在 JudgeService 里处理）。

        先过防通胀经济学层（噪声地板 → 负面权重/阶段乘数 → 同日重复衰减），
        归零则不产生任何变动与流水。规则引擎/跨插件 API 不走该层。
        message: 触发本次评估的用户发言原文（截断 200 字入库，供 WebUI 核对是否误判）。
        印象刷新由调用方（listeners）经 ImpressionService 触发。
        """
        group_id = self.group_key(group_id)
        if self._economy is not None:
            rec0 = await self.get(group_id, user_id)
            level_name = self.level_of(rec0.favor).name
            pos_today = await self._positive_judge_today(group_id, user_id)
            repair_factor = await self._repair_factor(group_id, user_id)
            eco = apply_economy(
                delta,
                level_name,
                pos_today,
                self._economy,
                repair=repair_factor is not None,
                repair_factor=repair_factor,
            )
            if eco.delta == 0:
                return FavorChange(
                    0,
                    reason,
                    "judge",
                    clamped=True,
                    favor_after=rec0.favor,
                    favor_before=rec0.favor,
                )
            delta = eco.delta
        change = await self._apply_one(
            group_id,
            user_id,
            delta,
            cooldown_key=None,
            cooldown_sec=0,
            reason=reason,
            source="judge",
            message=(message or "")[:200],
        )
        return change

    async def _positive_judge_today(self, group_id: str, user_id: str) -> int:
        """当日已生效的正向评审次数（供同日重复衰减）。失败返回 0。"""
        group_id = self.group_key(group_id)
        try:
            start = self._day_start()
            logs = await self._storage.query_logs(group_id, user_id, limit=50)
            return sum(
                1
                for r in logs
                if r.get("source") == "judge"
                and float(r.get("delta") or 0) > 0
                and float(r.get("ts") or 0) >= start
            )
        except Exception:
            return 0

    async def _repair_factor(self, group_id: str, user_id: str) -> float | None:
        """是否处于信任修复期（等级调制版）。

        重大得罪（≤ -repair_threshold、未撤销）按**冒犯时的等级**决定修复
        难度（Dirks 2011：高信任中的冒犯更难修）：深关系冒犯的窗口 ×
        repair_scale_high、压制更深；浅关系维持基线。任何失败返回 False。
        """
        group_id = self.group_key(group_id)
        eco = self._economy
        if eco is None or eco.repair_threshold <= 0 or eco.repair_hours <= 0:
            return None
        try:
            now = time.time()
            logs = await self._storage.query_logs(group_id, user_id, limit=50)
            for r in logs:
                if (
                    r.get("source") == "judge"
                    and not r.get("reversed")
                    and float(r.get("delta") or 0) <= -eco.repair_threshold
                ):
                    # 冒犯时的等级（用流水里的 before 值反查，无 schema 变化）
                    before = float(r.get("favor_before") or 0)
                    lvl = self.level_of(before).name
                    hours, factor = repair_params_for_level(lvl, eco)
                    if float(r.get("ts") or 0) >= now - hours * 3600:
                        return factor
            return None
        except Exception:
            return None

    async def _repair_window(self, group_id: str, user_id: str) -> bool:
        return await self._repair_factor(group_id, user_id) is not None

    async def change(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        reason: str = "api",
        source: str = "api",
    ) -> FavorChange:
        """通用增减入口（跨插件 API / 指令使用，无事件冷却，仍受每日限幅）。"""
        return await self._apply_one(
            group_id,
            user_id,
            delta,
            cooldown_key=None,
            cooldown_sec=0,
            reason=reason,
            source=source,
        )

    async def _apply_one(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        *,
        cooldown_key: str | None,
        cooldown_sec: int,
        reason: str,
        source: str,
        message: str = "",
    ) -> FavorChange:
        group_id = self.group_key(group_id)
        if not math.isfinite(float(delta)):
            raise ValueError("关系变动必须是有限数值")
        now = time.time()
        # 1. 单事件冷却
        if cooldown_key and cooldown_sec > 0:
            last = await self._storage.last_event_at(group_id, user_id, cooldown_key)
            if last is not None and now - last < cooldown_sec:
                rec = await self.get(group_id, user_id)
                return FavorChange(
                    0,
                    reason,
                    source,
                    clamped=True,
                    favor_after=rec.favor,
                    favor_before=rec.favor,
                )
        # 2+3. 落库（存储层单事务：每日双向限幅 + min/max 封顶 + 1 位小数
        #    收敛 + 当日额度 + 流水 + 冷却，一个 commit——X-P1c 原子化；
        #    限幅读在锁内，并发写不会双双通过当日额度（Sourcery #66）；
        #    无记录时以 default_favor 为基数，而非 0；额度耗尽不动任何表）
        rec, real, allowed, capped = await self._storage.apply_favor_change(
            group_id,
            user_id,
            delta,
            max_favor=self.max_favor,
            min_favor=self.min_favor,
            decay=self._decay,
            effective=(self._effective if self._decay is not None else None),
            default_favor=self.default_favor,
            day=self._today().isoformat(),
            reason=reason,
            source=source,
            message=message,
            cooldown_key=cooldown_key,
            daily_cap_up=self.daily_cap_up,
            daily_cap_down=self.daily_cap_down,
        )
        if real == 0 and allowed == 0:
            # 当日额度耗尽：与历史行为一致（clamped=True、零变动、不写流水）
            return FavorChange(
                0,
                reason,
                source,
                clamped=True,
                favor_after=rec.favor,
                favor_before=rec.favor,
            )
        return FavorChange(
            real,
            reason,
            source,
            clamped=capped or real != allowed,
            favor_after=rec.favor,
            # 权威基线：与 add_log 同源（落库后回减 real），零漂移
            favor_before=round1(rec.favor - real),
        )

    # ---------- 管理 ----------

    async def set_favor(
        self,
        group_id: str,
        user_id: str,
        value: float,
        source: str = "admin",
        reason: str = "set",
    ) -> FavorRecord:
        """管理员直接设值：绕过每日限幅，数值与流水单事务提交。"""
        group_id = self.group_key(group_id)
        if not math.isfinite(float(value)):
            raise ValueError("关系数值必须是有限数值")
        value = round1(max(self.min_favor, min(self.max_favor, float(value))))
        return await self._storage.set_value_with_log(
            group_id,
            user_id,
            value,
            reason=reason,
            source=source,
            default_favor=self.default_favor,
            effective=(self._effective if self._decay is not None else None),
        )

    async def undo_preview(self, log_id: int) -> dict:
        """预览撤销某条变动的结果（不写库）：返回当前好感与撤销后好感等。"""
        log = await self._storage.get_log(log_id)
        if log is None:
            raise ValueError("记录不存在")
        if log.get("reversed"):
            raise ValueError("该变动已撤销")
        group_id, user_id = log["group_id"], log["user_id"]
        cur = (await self.get(group_id, user_id)).favor
        state = await self.get(group_id, user_id)
        vector = log.get("relation_delta") or {}
        relation_after = dict(
            familiarity=round1(
                max(0, min(100, state.familiarity - vector.get("familiarity", 0)))
            ),
            affinity=round1(
                max(self.min_favor, min(self.max_favor, cur - float(log["delta"])))
            ),
            trust=round1(max(-100, min(100, state.trust - vector.get("trust", 0)))),
        )
        return {
            "relation_after": relation_after,
            "current": cur,
            "after": round1(
                max(self.min_favor, min(self.max_favor, cur - float(log["delta"])))
            ),
            "delta": float(log["delta"]),
            "reason": log.get("reason") or "",
            "message": log.get("message") or "",
        }

    async def undo_log(self, log_id: int) -> FavorRecord:
        """撤销某条变动：反向 delta 落地（标准 undo，不影响之后的其它变动）。

        整个"校验未撤销→反向→记账→标记"在 storage.apply_undo 单事务内完成
        （X4：拆开的检查-执行序列存在 TOCTOU，并发双击会双重反向扣分）。
        原流水标记 reversed=1（防重复撤销），并追加一条 source=undo 的反向
        流水；撤销一条 undo 行 = 重做原变动（对称）。撤销以衰减后的有效值
        为基数（effective 回调传入存储层，语义与旧实现一致）。
        """
        info = await self._storage.apply_undo(
            log_id,
            max_favor=self.max_favor,
            min_favor=self.min_favor,
            effective=(self._effective if self._decay is not None else None),
            default_favor=self.default_favor,
        )
        return await self.get(info["group_id"], info["user_id"])

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        group_id = self.group_key(group_id)
        await self._storage.reset(group_id, user_id)
