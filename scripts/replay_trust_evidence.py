"""真实服务合成流水的只读派生与合成标注上界；不调用 Provider。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import (
    EVENT_TYPES,
    RELATION_DIMENSIONS,
    RelationPolicy,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.migrations import MIGRATIONS
from astrbot_plugin_bond.storage.pragma_version import set_user_version
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter

FIXTURE = ROOT / "tests/fixtures/research/trust_evidence_shadow.json"
SUBJECT = dict(bot="10001", scope="per_group", group="7001", user="8001")
CATEGORIES = (
    "positive",
    "negative",
    "nondiagnostic",
    "unknown",
    "stale",
    "reversed",
    "manual_edit",
    "reversal_audit",
    "legacy_unknown",
)
WINDOW_DAYS = 30
NOW = 1_800_000_000.0


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def subject_key(subject):
    group = "__global__" if subject["scope"] == "global" else subject["group"]
    return "/".join((subject["bot"], subject["scope"], group, subject["user"]))


def source_view(sources, now, window_days=WINDOW_DAYS):
    """仅用保留流水派生；没有可靠性标签输入，不解析文本或猜测历史 T 语义。"""
    entries = []
    for source in sources:
        row = source["row"]
        vector = row["relation_delta"]
        if row["source"] in ("admin", "admin_profile"):
            category = "manual_edit"
        elif row["source"] == "undo":
            category = "reversal_audit"
        elif (
            row["source"] != "judge"
            or set(vector) != {"familiarity", "affinity", "trust"}
            or not row["event_id"]
        ):
            category = "legacy_unknown"
        elif row["reversed"]:
            category = "reversed"
        elif row["ts"] < now - window_days * 86400:
            category = "stale"
        else:
            category = "unknown"
        entries.append(
            dict(ref=source["ref"], event_key=source["event_key"], category=category)
        )
    return dict(
        source_refs=[s["ref"] for s in sources],
        source_sha256=digest(sources),
        entries=entries,
        known_logged_trust_delta_sum=round(
            sum(
                s["row"]["relation_delta"]["trust"]
                for s in sources
                if "trust" in s["row"]["relation_delta"]
            ),
            4,
        ),
        missing_trust_vector_refs=[
            s["ref"] for s in sources if "trust" not in s["row"]["relation_delta"]
        ],
        reliability_counts=None,
        history_evidence="unknown",
        coverage="retained_snapshot_only_not_all_past_messages",
    )


def annotated_category(entry, annotations):
    # 标签不能覆盖人工、撤销、陈旧及缺字段状态。
    if entry["category"] != "unknown":
        return entry["category"]
    return annotations.get(entry["event_key"], "unknown")


def shadow_counts(view, annotations):
    """独立合成标注的有来源计数；不是生产可计算的可靠性。"""
    counts = dict.fromkeys(CATEGORIES, 0)
    for entry in view["entries"]:
        counts[annotated_category(entry, annotations)] += 1
    return dict(
        source_refs=list(view["source_refs"]),
        source_sha256=view["source_sha256"],
        counts=counts,
    )


def shadow_summary(view, annotations):
    """按同一来源列举的确定性摘要，不生成新事实。"""
    groups = {
        category: [
            e["ref"]
            for e in view["entries"]
            if annotated_category(e, annotations) == category
        ]
        for category in CATEGORIES
    }
    text = "仅合成标注上界；非生产可知。\n" + "\n".join(
        f"{category}={len(refs)}: " + (", ".join(refs) if refs else "[]")
        for category, refs in groups.items()
    )
    return dict(
        source_refs=list(view["source_refs"]),
        source_sha256=view["source_sha256"],
        groups=groups,
        text=text,
    )


async def read_all(store, group=None, user=None):
    rows = []
    while True:
        page = await store.query_logs(group, user, limit=2, offset=len(rows))
        if not page:
            return rows
        rows.extend(page)


def wrap_rows(rows, subject):
    key = subject_key(subject)
    return [
        dict(
            ref=f"{key}/log/{r['id']}",
            event_key=f"{key}/{r['event_id']}" if r["event_id"] else None,
            row=r,
        )
        for r in rows
    ]


def make_legacy(root):
    """唯一直接插行的夹具：建立真实 v11，再交由 SQLiteBackend 迁移。"""
    path = root / "bots/10001/group.db"
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as conn:
        for version in range(1, 12):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            set_user_version(conn, version)
            conn.commit()
        conn.execute(
            "INSERT INTO favor(group_id,user_id,favor,updated_at) VALUES(?,?,?,?)",
            ("7001", "8001", 3, NOW),
        )
        conn.execute(
            "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,"
            "reason,source,ts,message) VALUES(?,?,?,?,?,?,?,?,?)",
            ("7001", "8001", 3, 0, 3, "合成旧日志", "judge", NOW, "合成旧片段"),
        )
        conn.commit()


async def run_case(root, case, historical_rules):
    if case.get("legacy"):
        make_legacy(root)
    router = BotScopeStorageRouter(root)
    clock = [NOW]
    subject = dict(SUBJECT)
    states = []

    async def service(target):
        store = await router.get(target["bot"], target["scope"])
        return FavorService(
            store,
            LevelTable.from_config(None),
            scope_mode=target["scope"],
            relation_policy=RelationPolicy(rules=historical_rules),
            decay_enabled=case.get("decay", False),
        )

    try:
        with patch("time.time", lambda: clock[0]):
            favor = await service(subject)
            for op in case["operations"]:
                if op["action"] == "advance":
                    clock[0] += op["days"] * 86400
                    continue
                target = dict(subject, **op.get("subject", {}))
                current = await service(target)
                args = (target["group"], target["user"])
                if op["action"] == "event":
                    await current.apply_relation_event(
                        *args,
                        op["event_type"],
                        event_id=op["event_id"],
                        intensity=op.get("intensity", 1),
                        cooldown_sec=0,
                        message="合成片段：" + op["event_id"],
                    )
                elif op["action"] == "manual":
                    await current.set_relation(
                        *args, **op["values"], event_id=op["event_id"]
                    )
                elif op["action"] == "profile":
                    await current.set_profile(
                        *args,
                        impression="合成管理员资料",
                        tags=[],
                        actor="synthetic-admin",
                    )
                elif op["action"] == "undo":
                    original = next(
                        row
                        for row in await read_all(
                            current._storage, current.group_key(args[0]), args[1]
                        )
                        if row["event_id"] == op["event_id"]
                    )
                    await current.undo_log(original["id"])
                elif op["action"] == "reset":
                    await current.reset(*args)
                else:
                    raise ValueError("未知合成操作：" + op["action"])
                states.append(
                    await favor.formal_relation(subject["group"], subject["user"])
                )

            rows = await read_all(
                favor._storage, favor.group_key(subject["group"]), subject["user"]
            )
            sources = wrap_rows(rows, subject)
            inventory = []
            for (bot, scope), store in sorted(router._backends.items()):
                for row in await read_all(store):
                    target = dict(
                        bot=bot, scope=scope, group=row["group_id"], user=row["user_id"]
                    )
                    inventory.extend(wrap_rows([row], target))
            changes_before = sum(
                s._c().total_changes for s in router._backends.values()
            )
            view = source_view(sources, clock[0])
            counts = shadow_counts(view, case["annotations"])
            summary = shadow_summary(view, case["annotations"])
            changes_after = sum(s._c().total_changes for s in router._backends.values())
            keys = {s["event_key"] for s in sources}
            checks = dict(
                same_sources=counts["source_refs"]
                == summary["source_refs"]
                == view["source_refs"],
                same_content=counts["source_sha256"]
                == summary["source_sha256"]
                == digest(sources),
                counts_match_summary=all(
                    counts["counts"][c] == len(summary["groups"][c]) for c in CATEGORIES
                ),
                all_sources_accounted=sum(counts["counts"].values()) == len(sources)
                and len({r for refs in summary["groups"].values() for r in refs})
                == len(sources),
                no_analysis_writes=changes_after == changes_before,
            )
            return dict(
                id=case["id"],
                subject=subject_key(subject),
                now=clock[0],
                formal_relation=await favor.formal_relation(
                    subject["group"], subject["user"]
                ),
                states=states,
                sources=sources,
                inventory=inventory,
                production_derived=view,
                synthetic_upper_bound=dict(
                    provenance="synthetic_external_annotations_not_production_observable",
                    production_observable=False,
                    counts=counts,
                    summary=summary,
                    annotations_without_retained_source=sorted(
                        set(case["annotations"]) - keys
                    ),
                ),
                analysis_sqlite_changes=changes_after - changes_before,
                checks=checks,
            )
    finally:
        await router.close()


async def experiment(runtime):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if (
        data["format_version"] != 1
        or data["provenance"]
        != "synthetic_external_annotations_not_production_observable"
    ):
        raise ValueError("合成夹具版本或来源标记无效")
    rules = data["historical_rules"]
    if set(rules) != set(EVENT_TYPES) or any(
        set(vector) != set(RELATION_DIMENSIONS) for vector in rules.values()
    ):
        raise ValueError("历史规则快照必须完整覆盖全部事件及 F/A/T，禁止回退到生产默认")
    names = [case["id"] for case in data["cases"]]
    if len(names) != len(set(names)) or any(
        not name or any(c not in "abcdefghijklmnopqrstuvwxyz_0123456789" for c in name)
        for name in names
    ):
        raise ValueError("合成场景 ID 必须唯一且只能含小写字母、数字和下划线")
    for case in data["cases"]:
        if set(case["annotations"].values()) - {
            "positive",
            "negative",
            "nondiagnostic",
        }:
            raise ValueError("未知合成标注")
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=False)
    results = [
        await run_case(runtime / case["id"], case, rules) for case in data["cases"]
    ]
    check_names = results[0]["checks"]
    return dict(
        format_version=1,
        phase0_base="db0624139b177cb3fc70047fd214ac564c75a5d5",
        fixture_sha256=digest(data),
        historical_rules=data["historical_rules"],
        historical_rules_sha256=digest(rules),
        historical_rules_source=data["historical_rules_source"],
        historical_rules_note=data["historical_rules_note"],
        source_files_sha256={
            name: hashlib.sha256(
                (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for name in (
                "services/favor_service.py",
                "storage/sqlite_backend.py",
                "storage/migrations.py",
                "core/relation_policy.py",
                "scripts/replay_trust_evidence.py",
            )
        },
        window_days=WINDOW_DAYS,
        evaluation="synthetic_real_sqlite_derivation_consistency_only",
        provider_calls=0,
        semantic_gain=None,
        persistent_false_update_gain=None,
        production_decision="stop_no_proven_production_benefit",
        checks={
            name: dict(
                numerator=sum(c["checks"][name] for c in results),
                denominator=len(results),
            )
            for name in check_names
        },
        cases=results,
    )


def markdown_report(report):
    lines = [
        "# Trust 离线流水派生对照",
        "",
        "仅真实服务生成的合成 SQLite；无 Provider、概率或新持久状态。",
        "仅凭流水的可靠性计数均未知。合成外部标注只示范信息上界，非生产可知。",
        "两臂使用同一来源及内容指纹；摘要是计数的确定性列举，不证明语义收益。",
        report["historical_rules_note"],
        f"历史规则 SHA-256：`{report['historical_rules_sha256']}`；完整规则及出处见 results.json。",
        "",
        "| 场景 | 来源行 | 当前 T | 已知流水 T 增量和 | 上界正/负 | 未标注/旧字段 | 失效/人工等 |",
        "| --- | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for case in report["cases"]:
        counts = case["synthetic_upper_bound"]["counts"]["counts"]
        lines.append(
            f"| {case['id']} | {len(case['sources'])} | {case['formal_relation']['trust']} | "
            f"{case['production_derived']['known_logged_trust_delta_sum']} | "
            f"{counts['positive']}/{counts['negative']} | {counts['unknown']}/{counts['legacy_unknown']} | "
            f"{sum(counts[c] for c in ('stale', 'reversed', 'manual_edit', 'reversal_audit', 'nondiagnostic'))} |"
        )
    lines.extend(["", "检查（分子/分母，单位为场景）："])
    lines.extend(
        f"- {name}: {v['numerator']}/{v['denominator']}"
        for name, v in report["checks"].items()
    )
    lines.extend(
        [
            "",
            "零计数仅指保留来源内该类条目，不能证明完整历史 E=0；reset 删除的来源无法恢复。",
            "语义收益、持久化误更新收益均未知。结论：停止生产推进，不提出 schema RFC。",
            "精确来源引用、各类排除项及合成标注缺源情况见 results.json。",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="新的离线输出目录")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请指定新的目录")
    args.output.mkdir(parents=True)
    report = asyncio.run(experiment(args.output / "runtime"))
    (args.output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    text = markdown_report(report)
    (args.output / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)
    if not all(v["numerator"] == v["denominator"] for v in report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
