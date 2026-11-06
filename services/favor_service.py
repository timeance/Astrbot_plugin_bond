"""心弦好感度 - 好感度应用服务。

增减、查询、防刷（单事件冷却 + 每日双向限幅）。
所有变化都经这里收口，是好感度数值管理的唯一入口。
成员印象的维护在 ImpressionService（v1.29.3 拆出）。
"""

from __future__ import annotations

import time
import math
from datetime import date, datetime, timedelta

from ..core.decay import HALF_LIFE_MIN, decay_target, effective_favor, resolve_half_life
from ..core.decimal import round1
from ..core.identity import is_master as _is_master
from ..core.levels import LevelTable
from ..core.level_economy import EconomyConfig, apply as apply_economy
from ..core.level_economy import (
    repair_params_for_level,
    FamiliarityConfig,
    familiarity_multiplier,
)
from ..core.relation_policy import affinity_bounds_for_undo
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
        diagnostics=None,
        familiarity_growth=None,
        trust_decay_enabled=False,
        trust_half_life=180.0,
        familiarity_decay_enabled=False,
        familiarity_half_life=60.0,
        familiarity_baseline=0.0,
        trust_baseline=0.0,
        decay_grace_days: float = 5.0,
        maintenance_strategy: str = "independent",
    ) -> None:
        from ..core.relation_policy import RelationPolicy, RelationResolver

        self._relation_policy = relation_policy or RelationPolicy()
        self._relation_resolver = relation_resolver or RelationResolver()
        self._relation_caps = relation_caps or dict(
            familiarity=(5, 0), affinity=(10, 8), trust=(2, 5)
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
        if (
            isinstance(decay_grace_days, bool)
            or not isinstance(decay_grace_days, (int, float))
            or not math.isfinite(decay_grace_days)
            or decay_grace_days < 0
        ):
            raise ValueError("关系保温期须为有限非负天数")
        if maintenance_strategy not in ("independent", "legacy"):
            raise ValueError("关系维护策略须为 independent 或 legacy")
        self.maintenance_strategy = maintenance_strategy
        self._decay_grace_days = float(decay_grace_days)
        self.diagnostics = diagnostics
        self._familiarity_growth = FamiliarityConfig.from_config(familiarity_growth)
        self._familiarity_half_life = (
            max(HALF_LIFE_MIN, float(familiarity_half_life))
            if familiarity_decay_enabled
            else None
        )
        self._trust_half_life = (
            max(HALF_LIFE_MIN, float(trust_half_life)) if trust_decay_enabled else None
        )
        # 先验证幅度；关闭衰减时也不接受非法配置。
        for amplitude in (decay_baseline, familiarity_baseline, trust_baseline):
            decay_target(0, amplitude)
        self._familiarity_baseline = float(familiarity_baseline)
        self._trust_baseline = float(trust_baseline)
        self._diagnostic_config = {}
        if diagnostics is not None and diagnostics.enabled:
            import hashlib
            import json
            from dataclasses import asdict
            from ..core.relation_lexicon import LEXICON_VERSION

            self._diagnostic_config = dict(
                policy_version="0.4.5",
                lexicon_version=LEXICON_VERSION,
                config_hash=hashlib.sha256(
                    json.dumps(
                        dict(
                            rules=self._relation_policy.snapshot(),
                            caps=self._relation_caps,
                            familiarity_growth=asdict(self._familiarity_growth),
                            trust_decay=[
                                trust_decay_enabled,
                                trust_half_life,
                                trust_baseline,
                            ],
                            familiarity_decay=[
                                familiarity_decay_enabled,
                                familiarity_half_life,
                                familiarity_baseline,
                            ],
                            economy=asdict(economy) if economy is not None else None,
                            decay_grace_days=self._decay_grace_days,
                            maintenance_strategy=maintenance_strategy,
                            timezone=tz_name,
                            floor=decay_floor_enabled,
                            initial=[min_favor, max_favor, default_favor],
                            decay=[
                                decay_enabled,
                                half_life_base,
                                half_life_growth,
                                half_life_max,
                                decay_baseline,
                            ],
                        ),
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            )
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
        self._storage.configure_initial_values(
            base if decay_enabled else 10.0, default_favor=self.default_favor
        )
        self._relationships = relationships
        self._economy = economy
        self._nick_cache: dict[tuple[str, str], str] = {}
        self._storage.configure_maintenance(
            maintenance_strategy, self._effective_record
        )
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

    @property
    def diagnostics_enabled(self):
        return self.diagnostics is not None and self.diagnostics.enabled

    def _diagnostic_failure(self):
        try:
            if self.diagnostics_enabled:
                self.diagnostics.note_failure(self._storage.bot_id, self.scope_mode)
        except Exception:
            pass  # 观察点本身不可用时也不能改变已提交业务结果。

    def record_diagnostic(self, group_id, user_id, kind, **fields):
        try:
            if self.diagnostics_enabled:
                if fields.get("event_id") is not None:
                    import hashlib

                    fields["event_id"] = hashlib.sha256(
                        str(fields["event_id"]).encode()
                    ).hexdigest()
                fields = {
                    key: value
                    for key, value in fields.items()
                    if value is not None or key == "tokens"
                }
                from ..core.scope import is_account_id

                # 合成全局键不能当来源群，缺来源只计记录缺口。
                if not is_account_id(str(group_id or "")):
                    self._diagnostic_failure()
                    return
                from .diagnostic_capture import CURRENT

                capture = CURRENT.get()
                if capture is not None and (
                    capture.recorder is self.diagnostics
                    and capture.bot == self._storage.bot_id
                    and capture.mode == self.scope_mode
                    and capture.group == str(group_id)
                    and capture.user == str(user_id)
                ):
                    fields.update(
                        trace_id=capture.trace_id, generation=capture.generation
                    )
                self.diagnostics.emit(
                    self._storage.bot_id,
                    self.scope_mode,
                    group_id,
                    user_id,
                    kind,
                    **self._diagnostic_config,
                    **fields,
                )
        except Exception:
            self._diagnostic_failure()

    def _diagnose_change(
        self, group_id, user_id, result, reason, event_id, event_type=None
    ):
        try:
            if not self.diagnostics_enabled:
                return
            before = self.describe_relation(**result["before"])
            after = self.describe_relation(**result["after"])
            self.record_diagnostic(
                group_id,
                user_id,
                "relation",
                reason=reason,
                event_id=event_id,
                event_type=event_type,
                before=result["before"],
                after=result["after"],
                delta=result["delta"],
                bands_before=before.bands or None,
                bands_after=after.bands or None,
                term_before=before.key,
                term_after=after.key,
                status=result["skipped"] or "committed",
                **result.get("maintenance", {}),
            )
        except Exception:
            self._diagnostic_failure()

    def invalidate_cache(self):
        """恢复数据库后重新核对成员是否存在及昵称。"""
        self._nick_cache.clear()

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
        self, stored: float, updated_at: float, half_life: float = 10.0, *, rounded=True
    ) -> float:
        """读取时的有效好感度（启用衰减时按指数遗忘曲线向基线收敛）。

        固定模式直接取当前基础半衰期；巩固模式把历史值限制在当前范围内。
        配置重载后从原时间锚重新计算，不为配置变化批量改库或生成事件。
        """
        if self._decay is None:
            return round1(stored) if rounded else float(stored)
        base, growth, h_max, amplitude = self._decay
        baseline = decay_target(stored, amplitude)
        return effective_favor(
            stored,
            updated_at,
            time.time(),
            half_life=resolve_half_life(
                half_life, base=base, growth=growth, h_max=h_max
            ),
            baseline=baseline,
            floor=self._decay_floor(stored, baseline),
            rounded=rounded,
            grace_days=self._decay_grace_days,
        )

    def _effective_record(self, rec, now=None):
        """同一记录按结算时点和维护截止点读取；读取不移动锚点或托底参考。"""
        now = time.time() if now is None else now
        result = {}
        for key, value, settled, maintained, half_life, amplitude in (
            (
                "affinity",
                rec.favor,
                rec.updated_at,
                rec.affinity_maintained_at,
                (
                    resolve_half_life(
                        rec.half_life,
                        base=self._decay[0],
                        growth=self._decay[1],
                        h_max=self._decay[2],
                    )
                    if self._decay
                    else None
                ),
                self._decay[3] if self._decay else 0,
            ),
            (
                "familiarity",
                rec.familiarity,
                rec.familiarity_updated_at,
                rec.familiarity_maintained_at,
                self._familiarity_half_life,
                self._familiarity_baseline,
            ),
            (
                "trust",
                rec.trust,
                rec.trust_updated_at,
                rec.trust_maintained_at,
                self._trust_half_life,
                self._trust_baseline,
            ),
        ):
            if half_life is None or settled <= 0:
                result[key] = float(value)
                continue
            maintenance = settled if maintained is None else min(settled, maintained)
            start = max(settled, maintenance + self._decay_grace_days * 86400)
            target = decay_target(value, amplitude)
            floor = None
            if key == "affinity":
                reference = rec.affinity_floor_reference
                floor = self._decay_floor(
                    value if reference is None else reference, target
                )
            result[key] = effective_favor(
                value,
                start,
                now,
                half_life=half_life,
                baseline=target,
                floor=floor,
                rounded=False,
            )
            # 数值已在地板上时，分段结算仍须停在同一地板，不能再次向下衰减。
            if floor is not None and value >= floor > target:
                result[key] = max(result[key], floor)
        return result

    def _effective_affinity_raw(self, stored, updated_at, half_life=10.0):
        return self._effective(stored, updated_at, half_life, rounded=False)

    def _effective_familiarity(self, stored, updated_at):
        if self._familiarity_half_life is None:
            return float(stored)
        return effective_favor(
            stored,
            updated_at,
            time.time(),
            half_life=self._familiarity_half_life,
            baseline=decay_target(stored, self._familiarity_baseline),
            rounded=False,
            grace_days=self._decay_grace_days,
        )

    def _effective_trust(self, stored, updated_at):
        if self._trust_half_life is None:
            return float(stored)
        return effective_favor(
            stored,
            updated_at,
            time.time(),
            half_life=self._trust_half_life,
            baseline=decay_target(stored, self._trust_baseline),
            rounded=False,
            grace_days=self._decay_grace_days,
        )

    def _day_end(self, now=None):
        # 用本地日历的下一天，兼容夏令时的 23/25 小时日。
        next_day = self._today(now) + timedelta(days=1)
        return datetime(
            next_day.year, next_day.month, next_day.day, tzinfo=self._tz
        ).timestamp()

    async def today_changes(self, *, now=None):
        """当前空间当天实际长期变动人数；时间口径与关系额度一致。"""
        now = time.time() if now is None else now
        return dict(
            day=self._today(now).isoformat(),
            timezone=str(self._tz or datetime.fromtimestamp(now).astimezone().tzinfo),
            reset_at=self._day_end(now),
            changed_members=await self._storage.changed_members_count(
                self._day_start(now), self._day_end(now)
            ),
        )

    async def get(self, group_id: str, user_id: str) -> FavorRecord:
        """读取记录；无记录时返回默认值（不落库）。返回的是有效好感度（含时间衰减）。"""
        group_id = self.group_key(group_id)
        temporary = await self._storage.override_state(group_id, user_id)
        rec = await self._storage.get(group_id, user_id)
        return self._snapshot_record(group_id, user_id, rec, temporary, time.time())

    def _snapshot_record(self, group_id, user_id, rec, temporary, now):
        """只投影返回副本；普通查询与消息快照共用默认值、衰减和临时关系语义。"""
        if rec is None:
            rec = FavorRecord(
                group_id=group_id,
                user_id=user_id,
                favor=self.default_favor,
                updated_at=0.0,
            )
        else:
            current = self._effective_record(rec, now)
            rec.favor = round1(current["affinity"])
            rec.trust = round1(current["trust"])
            rec.familiarity = round1(current["familiarity"])
        if temporary["active"]:
            rec.favor = temporary["values"]["affinity"]
            rec.familiarity = temporary["values"]["familiarity"]
            rec.trust = temporary["values"]["trust"]
        return rec

    async def read_relation_snapshot(
        self,
        group_id,
        user_id,
        *,
        count=3,
        days=7,
        sig_threshold=0.0,
        sig_window_mult=1.0,
    ):
        """一次一致读取；全局作用域不召回其他群的印象或流水。"""
        key = self.group_key(group_id)
        local = self.scope_mode == "per_group"
        rec, temporary, logs, now = await self._storage.read_relation_snapshot(
            key, user_id, log_limit=max(count * 5, count) if local and count > 0 else 0
        )
        rec = self._snapshot_record(key, user_id, rec, temporary, now)
        events = (
            await self.recent_events(
                key,
                user_id,
                count,
                days,
                sig_threshold=sig_threshold,
                sig_window_mult=sig_window_mult,
                logs=logs,
                now=now,
            )
            if local
            else []
        )
        if not local:
            rec.impression = ""
        return rec, events, now

    async def get_relation(self, group_id, user_id):
        from ..core.relation_policy import RelationState

        rec = await self.get(group_id, user_id)
        return RelationState(
            familiarity=rec.familiarity, affinity=rec.favor, trust=rec.trust
        )

    def describe_relation(self, familiarity, affinity, trust):
        from ..core.relation_policy import RelationState

        # 历史值只在称谓分类时饱和，不改数据库中的旧标量。
        return self._relation_resolver.describe(
            RelationState(
                familiarity=familiarity,
                affinity=max(-100, min(100, affinity)),
                trust=trust,
            )
        )

    def _relation_title(self, familiarity, affinity, trust):
        return self.describe_relation(familiarity, affinity, trust).title

    async def relation_title(self, group_id, user_id):
        rec = await self.get(group_id, user_id)
        return self._relation_title(rec.familiarity, rec.favor, rec.trust)

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
        expected_revision=None,
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
        original_intent = self._relation_policy.base_deltas(
            event_type, intensity, bounded=False
        )
        base_deltas = self._relation_policy.base_deltas(event_type, intensity)
        repair_factor = (
            await self._repair_factor(group_id, user_id)
            if self._economy is not None
            else None
        )

        def transform(before, positive_today):
            f_mult = familiarity_multiplier(
                before["familiarity"], self._familiarity_growth
            )
            deltas = dict(base_deltas)
            if deltas["familiarity"] > 0:
                deltas["familiarity"] *= f_mult
            if deltas["affinity"]:
                deltas["affinity"] = apply_economy(
                    deltas["affinity"],
                    self.level_of(before["affinity"]).name,
                    positive_today,
                    self._economy,
                    repair=repair_factor is not None,
                    repair_factor=repair_factor,
                    rounded=False,
                ).delta
            deltas = self._relation_policy.scale_positive(deltas, rounded=False)
            deltas = self._relation_policy.limit_deltas(
                event_type, deltas, familiarity_multiplier=f_mult, rounded=False
            )
            # 保留规则处理后的意图；事务层分别判定维护资格与一位小数计分。
            return deltas

        result = await self._storage.apply_relation_change(
            self.group_key(group_id),
            user_id,
            base_deltas,
            event_id=event_id,
            day=self._today().isoformat(),
            caps=self._relation_caps,
            cooldown_sec=cooldown_sec,
            expected_revision=expected_revision,
            reason=reason,
            message=message,
            effective=self._effective_affinity_raw,
            effective_trust=self._effective_trust,
            effective_familiarity=self._effective_familiarity,
            decay=self._decay,
            default_favor=self.default_favor,
            transform=transform,
            original_intent=original_intent,
            day_start=self._day_start(),
            day_end=self._day_end(),
            origin_group_id=str(group_id),
        )

        self._diagnose_change(
            group_id,
            user_id,
            result,
            "automatic",
            event_id,
            getattr(event_type, "value", event_type),
        )
        return result

    async def apply_passive_familiarity(
        self, group_id, user_id, *, trigger_id, observed_at=None, triggered_at=None
    ):
        """群聊活跃只增加 F，不授予维护资格，也不经过语义评估的倍率与冷却。"""
        from ..core.scope import account_id

        account_id(str(group_id))
        account_id(str(user_id))
        self._validate_event_id(trigger_id)
        now = time.time()
        triggered_at = now if triggered_at is None else triggered_at
        if observed_at is not None and (
            type(observed_at) not in (int, float)
            or not math.isfinite(observed_at)
            or type(triggered_at) not in (int, float)
            or not math.isfinite(triggered_at)
            or not 0 <= triggered_at - observed_at <= 600
            or not 0 <= now - triggered_at <= 600
        ):
            self.record_diagnostic(
                group_id, user_id, "relation", reason="passive", status="stale"
            )
            return {"skipped": "stale"}
        state = await self.temporary_state(group_id, user_id)
        if state["active"]:
            self.record_diagnostic(
                group_id, user_id, "relation", reason="passive", status="temporary"
            )
            return {"skipped": "temporary"}
        try:
            result = await self._storage.apply_passive_familiarity(
                self.group_key(group_id),
                str(user_id),
                event_id="passive:" + trigger_id,
                day=self._today(now).isoformat(),
                daily_cap_up=self._relation_caps["familiarity"][0],
                amount=0.3,
                ceiling=60.0,
                cooldown_sec=1800,
                expected_revision=state["revision"],
                day_start=self._day_start(now),
                day_end=self._day_end(now),
                origin_group_id=str(group_id),
            )
        except ValueError:
            # 暂停/恢复若发生在排队期间，旧候选不得穿越临时关系边界。
            current = await self.temporary_state(group_id, user_id)
            if current["active"] or current["revision"] != state["revision"]:
                return {"skipped": "temporary"}
            raise
        self._diagnose_change(group_id, user_id, result, "passive", trigger_id)
        return result

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
        result = await self._storage.apply_relation_change(
            self.group_key(group_id),
            user_id,
            dict.fromkeys(values, 0),
            event_id=event_id,
            day=self._today().isoformat(),
            caps=self._relation_caps,
            source="admin",
            reason=reason,
            values=values,
            effective=self._effective_affinity_raw,
            effective_trust=self._effective_trust,
            effective_familiarity=self._effective_familiarity,
            origin_group_id=str(group_id),
            default_favor=self.default_favor,
        )

        self._diagnose_change(group_id, user_id, result, "manual", event_id)
        return result

    async def set_profile(self, group_id, user_id, *, impression, tags, actor):
        """管理员资料编辑；值校验与审计共用一个服务入口。"""
        if not isinstance(impression, str) or len(impression) > 2000:
            raise ValueError("印象须为不超过 2000 字的文本")
        if (
            not isinstance(tags, list)
            or len(tags) > 3
            or any(
                not isinstance(t, str) or not 1 <= len(t.strip()) <= 20 for t in tags
            )
        ):
            raise ValueError("标签最多 3 个，每个 1..20 字")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("需要可信管理员身份")
        await self._storage.set_profile_audited(
            self.group_key(group_id),
            user_id,
            impression.strip(),
            [t.strip() for t in tags],
            actor,
        )

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        # 衰减后排序可能改变候选集，必须先按有效值重排再取前 N 名。
        if self.scope_mode == "global":
            rows = await self._storage.ranking_observed(group_id, -1)
        else:
            rows = await self._storage.ranking(group_id, -1)
        rows = [await self.get(r.group_id, r.user_id) for r in rows]
        names = await self._storage.member_names()
        for r in rows:
            r.nickname = names.get((group_id, r.user_id)) or names.get(
                ("", r.user_id), ""
            )
        for r in rows:
            r.nickname = names.get((group_id, r.user_id)) or names.get(
                ("", r.user_id), ""
            )
        rows.sort(key=lambda r: (-r.favor, r.user_id))
        return rows[:limit] if limit >= 0 else rows

    @staticmethod
    def last_maintained_at(rec):
        """最近真实维护时间；结算与策略切换不算互动。"""
        return max(
            settled if maintained is None else maintained
            for settled, maintained in (
                (rec.updated_at, rec.affinity_maintained_at),
                (rec.familiarity_updated_at, rec.familiarity_maintained_at),
                (rec.trust_updated_at, rec.trust_maintained_at),
            )
        )

    @classmethod
    def idle_days(cls, rec, now=None):
        last = cls.last_maintained_at(rec)
        now = time.time() if now is None else now
        return max(0, int((now - last) // 86400)) if last > 0 else None

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
        names = await self._storage.member_names()
        for stored in recs:
            r = await self.get(stored.group_id, stored.user_id)
            eff = r.favor
            last_maintained = self.last_maintained_at(r)
            idle = self.idle_days(r, now)
            out.append(
                {
                    "group_id": r.group_id,
                    "user_id": r.user_id,
                    "favor": eff,
                    "affinity": eff,
                    "familiarity": r.familiarity,
                    "trust": r.trust,
                    "relation_title": self._relation_title(r.familiarity, eff, r.trust),
                    "stored_favor": round1(stored.favor),
                    "decayed": (
                        round1(stored.favor) != eff
                        or round1(stored.familiarity) != r.familiarity
                        or round1(stored.trust) != r.trust
                    )
                    and not (await self.temporary_state(r.group_id, r.user_id))[
                        "active"
                    ],
                    "level": self.level_of(eff).name,
                    "relationship": (
                        self.relationship_label(r.relationship)
                        if r.relationship
                        else ""
                    ),
                    "nickname": (names.get((group_id, r.user_id)) if group_id else "")
                    or names.get(("", r.user_id), ""),
                    "impression": r.impression or "",
                    "tags": r.parsed_tags(),
                    "impression_at": r.impression_at,
                    "updated_at": last_maintained,
                    "idle_days": idle,
                }
            )
        out.sort(key=lambda x: x["favor"], reverse=True)
        return out[:limit] if limit >= 0 else out

    async def standings_page(
        self,
        group_id: str | None = None,
        *,
        page: int = 1,
        page_size: int = 50,
        search: str = "",
        include_zero: bool = False,
    ) -> dict:
        """先计算全部候选的有效态，再筛选、计数和分页，不能先截断存量值。"""
        if type(page) is not int or page < 1:
            raise ValueError("页码须为正整数")
        if type(page_size) is not int or page_size not in (30, 50, 100, 200):
            raise ValueError("每页条数须为 30、50、100 或 200")
        if not isinstance(search, str) or len(search) > 32:
            raise ValueError("成员搜索无效")
        if type(include_zero) is not bool:
            raise ValueError("展开选项无效")
        search = search.strip()
        rows = await self.standings(group_id, limit=-1)
        observed = await self._storage.observed_member_keys(group_id)
        observed_users = {user for _, user in observed}

        def foldable(row):
            seen = (
                row["user_id"] in observed_users
                if self.scope_mode == "global"
                else (row["group_id"], row["user_id"]) in observed
            )
            return seen and all(
                row[key] == 0 for key in ("familiarity", "affinity", "trust")
            )

        matched = [row for row in rows if search in row["user_id"]]
        zero_count = sum(foldable(row) for row in matched)
        # 搜索总能找回零值成员，不要求先展开。
        collapsed_count = 0 if include_zero or search else zero_count
        if collapsed_count:
            matched = [row for row in matched if not foldable(row)]
        matched.sort(
            key=lambda row: (-row["affinity"], row["group_id"], row["user_id"])
        )
        total = len(matched)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        users = matched[start : start + page_size]
        return dict(
            users=users,
            count=len(users),
            total=total,
            matched_count=total + collapsed_count,
            zero_count=zero_count,
            collapsed_count=collapsed_count,
            page=page,
            page_size=page_size,
            pages=pages,
        )

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
        await self._storage.update_member_names(group_id, user_id, card=nick)
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
        await self._storage.ensure_member(self.group_key(group_id), user_id)
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
        dimension: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """流水查询透传（注入链路一次拉取，milestone/recent_events 共用）。

        fuzzy=True 仅供面板搜索（子串匹配）；内部路径一律精确。"""
        if dimension not in (None, "familiarity", "affinity", "trust"):
            raise ValueError("未知流水维度")
        if source not in (None, "judge"):
            raise ValueError("未知流水来源筛选")
        return await self._storage.query_logs(
            self.group_key(group_id),
            user_id,
            limit=limit,
            offset=offset,
            fuzzy=fuzzy,
            dimension=dimension,
            source=source,
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
        now: float | None = None,
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
        rows = [
            r
            for r in rows
            if not r.get("reversed") and r.get("source") != "passive_familiarity"
        ]
        if days and days > 0:
            now = time.time() if now is None else now
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
                and not r.get("reversed")
                and float(r.get("delta") or 0) > 0
                and float(r.get("ts") or 0) >= start
            )
        except Exception:
            return 0

    async def _repair_factor(self, group_id: str, user_id: str) -> float | None:
        """可选的亲近度恢复抑制；默认关闭时不查询流水。

        已启用时扫描最近50条未撤销评估的负向变化，按旧亲近度档位调制。
        这不是自动信任修复；没有匹配记录或读取失败时返回 None。
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
            day_start=self._day_start(),
            day_end=self._day_end(),
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
        if log.get("source") == "admin_profile":
            raise ValueError("资料审计不是可撤销的数值变动")
        group_id, user_id = log["group_id"], log["user_id"]
        if (await self.temporary_state(group_id, user_id))["active"]:
            raise ValueError("临时关系生效或任务版本已过期，请结束临时关系后重试")
        state = await self.get(group_id, user_id)
        stored = await self._storage.get(self.group_key(group_id), user_id)
        if stored is not None:
            current = self._effective_record(stored)
            state.familiarity = current["familiarity"]
            state.favor = current["affinity"]
            state.trust = current["trust"]
        cur = state.favor
        vector = log.get("relation_delta") or {}
        affinity_min, affinity_max = affinity_bounds_for_undo(
            vector, legacy_min=self.min_favor, legacy_max=self.max_favor
        )
        relation_after = dict(
            familiarity=round1(
                max(0, min(100, state.familiarity - vector.get("familiarity", 0)))
            ),
            affinity=round1(
                max(
                    affinity_min,
                    min(affinity_max, cur - float(log["delta"])),
                )
            ),
            trust=round1(max(-100, min(100, state.trust - vector.get("trust", 0)))),
        )
        return {
            "relation_after": relation_after,
            "current": round1(cur),
            "after": round1(
                max(
                    affinity_min,
                    min(affinity_max, cur - float(log["delta"])),
                )
            ),
            "delta": float(log["delta"]),
            "reason": log.get("reason") or "",
            "message": log.get("message") or "",
        }

    async def undo_log(self, log_id: int, *, actor: str = "") -> FavorRecord:
        """撤销某条变动：反向 delta 落地（标准 undo，不影响之后的其它变动）。

        整个"校验未撤销→反向→记账→标记"在 storage.apply_undo 单事务内完成
        （X4：拆开的检查-执行序列存在 TOCTOU，并发双击会双重反向扣分）。
        原流水标记 reversed=1（防重复撤销），并追加反向流水；普通变动来源
        为 undo，被动变动沿用 passive_familiarity，整条撤销链不刷新保温。
        撤销一条反向流水 = 重做原变动（对称）。撤销以衰减后的有效值
        为基数（effective 回调传入存储层，语义与旧实现一致）。
        """
        info = await self._storage.apply_undo(
            log_id,
            max_favor=self.max_favor,
            min_favor=self.min_favor,
            effective=self._effective_affinity_raw,
            effective_trust=self._effective_trust,
            effective_familiarity=self._effective_familiarity,
            default_favor=self.default_favor,
            actor=actor,
        )
        self.record_diagnostic(
            info["group_id"],
            info["user_id"],
            "relation",
            reason="undo",
            status="committed",
        )
        return await self.get(info["group_id"], info["user_id"])

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        await self._storage.reset(self.group_key(group_id), user_id)
        self.record_diagnostic(
            group_id,
            user_id or "all-members",
            "relation",
            reason="reset",
            status="committed",
        )

    async def update_member_names(self, group_id, user_id, nickname=None, card=None):
        await self._storage.update_member_names(group_id, user_id, nickname, card)

    async def member_name(self, group_id, user_id):
        names = await self._storage.member_names()
        return (names.get((group_id, user_id)) if group_id else "") or names.get(
            ("", user_id), ""
        )

    async def temporary_state(self, group_id, user_id):
        return await self._storage.override_state(self.group_key(group_id), user_id)

    async def set_temporary(
        self, group_id, user_id, values, duration, actor, reason="管理员调整"
    ):
        from dataclasses import asdict
        from ..core.relation_policy import RelationState

        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("需要可信管理员身份")
        if not isinstance(reason, str) or len(reason) > 120:
            raise ValueError("调整原因不能超过 120 字")
        if values is not None:
            values = asdict(RelationState(**values))
            if (
                type(duration) not in (int, float)
                or not math.isfinite(duration)
                or not 60 <= duration <= 30 * 86400
            ):
                raise ValueError("临时时长须为 1 分钟至 30 天")
        await self._storage.set_override(
            self.group_key(group_id),
            user_id,
            values,
            duration,
            actor,
            self.default_favor,
            reason,
        )

        self.record_diagnostic(
            group_id,
            user_id,
            "relation",
            reason="temporary" if values is not None else "temporary_end",
            status="committed",
            temporary=values is not None,
        )

    async def temporary_audit(self, group_id, user_id):
        return await self._storage.override_audit(self.group_key(group_id), user_id)

    async def formal_relation(self, group_id, user_id):
        state = await self.temporary_state(group_id, user_id)
        rec = await self._storage.get(self.group_key(group_id), user_id)
        if rec is None:
            return dict(familiarity=0, affinity=self.default_favor, trust=0)
        # 正式关系在覆盖开始时暂停；同一结算/维护语义用于读值、预览和写入。
        now = time.time()
        point = min(now, state["started_at"]) if state["active"] else now
        return {
            key: round1(value)
            for key, value in self._effective_record(rec, point).items()
        }
