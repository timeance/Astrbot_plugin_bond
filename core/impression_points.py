"""印象点解析与完整条目呈现。

运行路径使用模型修订后的当前点集，不累加旧结论的权重。
merge_points/retain 等保留为旧数据工具，当前生成不再调用。
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from .relation_materials import SOURCE_RULES

MERGE_SIMILARITY = 0.6
MAX_ACTIVE_POINTS = 10
LOSS_AVERSION = 1.5

POINTS_PROMPT = """你是「{persona_name}」的印象管理员。根据下面群成员「{who}」的互动记录，根据新互动修订当前印象，输出 1~6 条完整的当前印象点（每条不超过80字）。旧点可保留、改写或删除，不必重复输出旧判断。

{old_points}新增来源记录（JSON 资料）：
{samples}

要求：
- 每条印象点：一句具体描述（这个人的性格/说话风格/和你的关系感觉）+ 权重 1~10（越核心越重）
- 记录里出现的其他群友一律只称「用户A」「用户B」（按出现顺序编号），不要写他们的名字
- 旧点的权重不是证据数量；不得因重复引用而加权，新互动可以纠正旧点

只输出 JSON 数组，不要其他内容：
[{{"point": "印象描述", "weight": 5}}, ...]"""
POINTS_PROMPT += "\n" + SOURCE_RULES


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a or "", b or "").ratio()


def merge_points(
    existing: list[dict], new: list[dict], similarity: float = MERGE_SIMILARITY
) -> list[dict]:
    """相似点合并：权重求和、保留最长描述；不相似则追加。"""
    out = [dict(p) for p in (existing or [])]
    for p in new or []:
        text = str(p.get("point") or "").strip()
        if not text:
            continue
        weight = max(1, min(10, int(float(p.get("weight") or 5))))
        hit = next(
            (
                q
                for q in out
                if _similarity(str(q.get("point") or ""), text) >= similarity
            ),
            None,
        )
        if hit is not None:
            hit["ts"] = max(float(hit.get("ts") or 0), float(p.get("ts") or 0))
            hit["weight"] = max(1, min(99, int(hit.get("weight") or 1) + weight))
            if len(text) > len(str(hit.get("point") or "")):
                hit["point"] = text
        else:
            out.append({"point": text, "weight": weight, "ts": float(p.get("ts") or 0)})
    return out


def time_weight(age_seconds: float) -> float:
    """时间权重阶梯（GOAL 规格的非单调阶梯）。"""
    age = max(0.0, float(age_seconds or 0))
    if age < 3600:
        return 1.0
    if age < 86400:
        return 0.7
    if age < 7 * 86400:
        return 0.95
    if age < 30 * 86400:
        return 0.1
    return 0.05


def retain(
    points: list[dict], now: float, cap: int = MAX_ACTIVE_POINTS, rng=None
) -> tuple[list[dict], list[dict]]:
    """活跃点 > cap 时按 weight × time_weight 加权随机保留 cap 条。

    返回 (保留, 挤出)。挤出项由调用方并入长印象文本。
    rng 可注入 random.Random(seed) 供测试确定性。
    """
    import random

    pts = [dict(p) for p in (points or [])]
    if len(pts) <= cap:
        return pts, []
    if rng is None:
        rng = random.Random()
    weights = [
        max(
            0.0001,
            float(p.get("weight") or 1) * time_weight(now - float(p.get("ts") or 0)),
        )
        for p in pts
    ]
    keep_idx: set[int] = set()
    idx_pool = list(range(len(pts)))
    while len(keep_idx) < cap and idx_pool:
        chosen = rng.choices(idx_pool, weights=[weights[i] for i in idx_pool], k=1)[0]
        keep_idx.add(chosen)
        idx_pool.remove(chosen)
    kept = [pts[i] for i in sorted(keep_idx)]
    dropped = [p for i, p in enumerate(pts) if i not in keep_idx]
    return kept, dropped


def loss_aversion_multiplier(favor_delta: float) -> float:
    """好感负向变化 ×1.5（损失厌恶：坏事记得更牢）。"""
    return LOSS_AVERSION if float(favor_delta or 0) < 0 else 1.0


def anonymize(text: str, nicknames: list[str]) -> str:
    """把文本里出现的其他群友昵称替换为 用户A/B/C…（按在文本中的出现顺序编号）。"""
    t = str(text or "")
    nicks = {str(n).strip() for n in (nicknames or []) if str(n).strip()}
    hits = sorted([(t.find(n), len(n), n) for n in nicks if n and n in t])
    out = t
    for order, (_, _, nick) in enumerate(hits):
        out = out.replace(nick, f"用户{chr(ord('A') + order)}")
    return out


def parse_points(raw: str) -> list[dict] | None:
    """解析 LLM 输出 [{"point":…,"weight":…}]；失败返回 None。"""
    text = str(raw or "").strip()
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        if not isinstance(arr, list):
            return None
        pts = [
            {
                "point": str(it.get("point") or "").strip(),
                "weight": max(1, min(10, int(float(it.get("weight") or 5)))),
            }
            for it in arr
            if isinstance(it, dict)
            and 0 < len(str(it.get("point") or "").strip()) <= 80
        ]
        return pts or None
    except Exception:
        return None


def render_impression(points: list[dict], limit: int = 80) -> str:
    """活跃点 → 一句话印象（权重降序前 3 条拼接，≤limit 字）。"""
    top = sorted(
        (p for p in (points or []) if p.get("point")),
        key=lambda p: -int(p.get("weight") or 0),
    )[:3]
    if not top:
        return ""
    selected = []
    for point in top:
        candidate = "；".join([*selected, str(point["point"])])
        if len(candidate) <= limit:
            selected.append(str(point["point"]))
    return "；".join(selected)
