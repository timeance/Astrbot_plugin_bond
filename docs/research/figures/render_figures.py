"""绘制 Bond 短论文配图；只读取归档 JSON，不运行模型或修改关系。"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager, pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

INK = "#172B3A"
MUTED = "#526575"
TEAL = "#087F8C"
BLUE = "#3878BD"
CORAL = "#C35C43"
PURPLE = "#775FA1"
PALE = "#F2F6F8"
ROOT = Path(__file__).resolve().parent.parent


def text(ax, x, y, label, size=16, color=INK, weight="normal", **kw):
    return ax.text(
        x,
        y,
        label,
        fontsize=size,
        color=color,
        weight=weight,
        va="top",
        linespacing=1.6,
        **kw,
    )


def canvas(title, subtitle, height=8.4):
    fig, ax = plt.subplots(figsize=(14, height), facecolor="white")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    ax.set(xlim=(0, 140), ylim=(0, 84))
    ax.axis("off")
    text(ax, 6, 79, title, 25, weight="bold")
    text(ax, 6, 71, subtitle, 13, MUTED)
    return fig, ax


def box(ax, x, y, w, h, color=PALE, edge="none"):
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.4,rounding_size=1.3",
            facecolor=color,
            edgecolor=edge,
            linewidth=1.2,
        )
    )


def arrow(ax, start, end):
    ax.add_patch(
        FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=15, linewidth=1.7, color=MUTED
        )
    )


def save(fig, output, name, description):
    fig.savefig(output / f"{name}.png", dpi=160, facecolor="white")
    fig.savefig(
        output / f"{name}.svg",
        facecolor="white",
        metadata={"Date": None, "Description": description},
    )
    svg = output / f"{name}.svg"
    svg.write_text(
        "\n".join(
            line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def dimensions(output):
    fig, ax = canvas(
        "关系由三个不同问题组成",
        "机制示意  /  F、A、T 是交互策略状态，不是人的心理测量结果",
    )
    cards = [
        (
            6,
            TEAL,
            "F  熟悉度",
            "Familiarity · 0 至 100",
            "是否已经认识这位成员？",
            "依据：确认的互动接触\n不直接推断喜欢或可靠",
        ),
        (
            50,
            CORAL,
            "A  亲近度",
            "Affinity · −100 至 100",
            "愿意以多近的距离交流？",
            "依据：温暖、合作或敌意\n不直接证明陈述真实",
        ),
        (
            94,
            PURPLE,
            "T  信任度",
            "Trust · −100 至 100",
            "对可靠性持什么态度？",
            "礼貌、示好、道歉默认不增信\n实际行为修复仍待验证",
        ),
    ]
    for x, color, title, subtitle, question, body in cards:
        box(ax, x, 34, 39, 31)
        text(ax, x + 2, 62, title, 21, color, "bold")
        text(ax, x + 2, 55, subtitle, 11, MUTED)
        text(ax, x + 2, 49, question, 14, weight="bold")
        text(ax, x + 2, 43, body, 12, MUTED)
    text(ax, 6, 27, "组合读法", 16, weight="bold")
    box(ax, 6, 13, 61, 9, "#EAF5F3")
    box(ax, 72, 13, 61, 9, "#F2EFF7")
    text(ax, 8, 20, "熟悉 + 亲近 + 戒备：聊得来，仍有边界", 13, TEAL)
    text(ax, 74, 20, "熟悉 + 疏远 + 信赖：不热络，仍可放心", 13, PURPLE)
    text(
        ax,
        6,
        7,
        "T 的中间值不能区分缺证据与正负抵消；当前没有独立的证据量字段。",
        12,
        MUTED,
    )
    save(
        fig,
        output,
        "bond-dimensions",
        "三维独立含义与两种混合态度；概念图，无测量数据。",
    )


def pipeline(output):
    fig, ax = canvas(
        "从群消息到关系表达",
        "实现快照  /  平台事实约束归因，模型只提供建议，本地程序控制持久化",
        height=9,
    )
    xs = [5, 39, 73, 107]
    top = [
        ("01  平台事实", "Bot / 发言者 / 消息 ID\n类型化 At 与 Reply", TEAL),
        ("02  本地准入", "目标冲突、寒暄过滤\n去重、冷却、并发检查", TEAL),
        ("03  模型分类", "事件类别 + 置信度\n当前消息原文证据", BLUE),
        ("04  校验与复核", "严格协议、原文子串\n必要复核；可弃权", BLUE),
    ]
    bottom = [
        ("08  人格表达", "保留当前人格与历史\n不改接话、权限边界", CORAL),
        ("07  称谓与释义", "5 × 5 × 5 个组合\n55 个词条保留混合态度", PURPLE),
        ("06  三维状态", "F / A / T 分开保存\n按 Bot 与作用域隔离", PURPLE),
        ("05  规则与事务", "事件映射、限幅与去重\n实际变化及流水原子提交", CORAL),
    ]
    for y, row in [(44, top), (16, bottom)]:
        for x, (title, body, color) in zip(xs, row):
            box(ax, x, y, 28, 21)
            text(ax, x + 1.7, y + 18, title, 15, color, "bold")
            text(ax, x + 1.7, y + 11.5, body, 11, MUTED)
    for i in range(3):
        arrow(ax, (xs[i] + 28.7, 54), (xs[i + 1] - 0.8, 54))
        arrow(ax, (xs[i + 1] - 0.8, 26), (xs[i] + 28.7, 26))
    arrow(ax, (121, 43), (121, 38))
    text(ax, 5, 40, "后台评估结果供后续读取；主回复不等待评估完成。", 12, MUTED)
    box(ax, 5, 3.5, 130, 8, "#FFF4EA")
    text(
        ax,
        7,
        9.5,
        "失败或不确定 → 本次关系不更新。原文匹配不能证明作者立场或陈述真实性。",
        12,
        CORAL,
    )
    save(
        fig,
        output,
        "bond-pipeline",
        "八步流程及弃权边界；异步关系更新不要求当前回复等待。",
    )


def archived_metrics():
    phase0 = json.loads((ROOT / "PHASE0_RESULTS.json").read_text(encoding="utf-8"))
    phase1 = json.loads((ROOT / "PHASE1_RESULTS.json").read_text(encoding="utf-8"))
    phase2 = json.loads((ROOT / "PHASE2_RESULTS.json").read_text(encoding="utf-8"))
    if len({d["fixture_sha256"] for d in (phase0, phase1, phase2)}) != 1:
        raise ValueError("三个归档不是同一夹具，不能生成此对照图")
    rows = [
        phase0["arms"]["baseline"]["metrics"],
        phase1["right"]["metrics"],
        phase2["right"]["metrics"],
    ]
    if len({d["events"] for d in rows}) != 1:
        raise ValueError("各阶段事件数不一致")
    return rows


def replay(output):
    rows = archived_metrics()
    before, after = rows[0], rows[2]
    specs = [
        (
            "trust_false_update_rate",
            "信任本应保持不变",
            "例：成员对 Bot 说“谢谢你”\n亲近度可增加，信任不因此增加",
        ),
        (
            "target_false_update_rate",
            "消息未明确指向当前 Bot",
            "例：成员在感谢另一位成员\nBot 不把它算作自己的互动",
        ),
        (
            "persistent_false_update_rate",
            "关系各维度都应保持不变",
            "例：只说一句“早安”\n不据此改变长期关系",
        ),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 8.6), facecolor="white")
    fig.subplots_adjust(left=0.085, right=0.975, bottom=0.39, top=0.71, wspace=0.42)
    fig.text(
        0.04,
        0.94,
        "关系调整准确性：该不变时，能否保持不变",
        fontsize=25,
        weight="bold",
        color=INK,
    )
    fig.text(
        0.04,
        0.865,
        "正确保持不变的比例 · 越高越好 · 修改前与修改后使用同一批测试消息",
        fontsize=14,
        color=MUTED,
    )
    for ax, (key, title, example) in zip(axes, specs):
        denominators = {row[key]["denominator"] for row in rows}
        if len(denominators) != 1:
            raise ValueError(f"{key} 的分母不一致")
        denominator = denominators.pop()
        if denominator <= 0:
            raise ValueError(f"{key} 没有可计分样本")
        correct = [denominator - row[key]["numerator"] for row in (before, after)]
        if any(not 0 <= count <= denominator for count in correct):
            raise ValueError(f"{key} 的计数超出分母")
        percentages = [100 * count / denominator for count in correct]
        ax.barh([1, 0], percentages, color=["#748494", TEAL], height=0.45)
        ax.set_yticks([1, 0], ["修改前", "修改后"], color=INK, fontsize=12)
        ax.set_xlim(0, 100)
        ax.set_ylim(-0.5, 1.5)
        ax.set_title(title, fontsize=14, color=INK, loc="left", pad=22, weight="bold")
        ax.set_xticks([0, 50, 100], ["0%", "50%", "100%"])
        ax.tick_params(axis="both", length=0, colors=MUTED, labelsize=11)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#E5EBEF", linewidth=0.8)
        for y, count, percentage in zip([1, 0], correct, percentages):
            ax.text(
                3,
                y,
                f"{percentage:.1f}%  ·  {count}/{denominator}",
                va="center",
                fontsize=13,
                color="white",
                weight="bold",
            )
        ax.text(
            0,
            -0.28,
            example,
            transform=ax.transAxes,
            fontsize=12,
            color=INK,
            va="top",
            linespacing=1.7,
        )
    fig.text(
        0.04,
        0.19,
        "修改后：调整事件规则与寒暄过滤，并加入受话人冲突检查。条内数字为正确次数 / 该组测试次数。",
        fontsize=12,
        color=MUTED,
    )
    fig.text(
        0.04,
        0.13,
        "这里只检验“不该变动时是否保持不变”，不代表“该增减时是否调得对”或整体准确率。",
        fontsize=12,
        color=CORAL,
    )
    fig.text(
        0.04,
        0.075,
        "人工编写 52 个场景、1201 条消息，模型回复预先设定。分组有重叠，不合并为总分；非真实群聊测量。",
        fontsize=11,
        color=MUTED,
    )
    fig.text(
        0.04,
        0.03,
        "数据：PHASE0_RESULTS.json 的原基线 → PHASE2_RESULTS.json 的 current；中间阶段数据见正文。",
        fontsize=10,
        color=MUTED,
    )
    save(
        fig,
        output,
        "bond-replay-results",
        "关系调整的局部正确性：本应不变的样本中正确保持不变的比例，比较归档原基线与 Phase2。",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font", help="已安装的中文字体名称")
    args = parser.parse_args()
    candidates = (
        [args.font]
        if args.font
        else ["Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"]
    )
    available = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((font for font in candidates if font in available), None)
    if selected is None:
        raise SystemExit("找不到中文字体，请安装中文字体或使用 --font 指定")
    matplotlib.rcParams.update(
        {
            "font.family": selected,
            "axes.unicode_minus": False,
            "svg.fonttype": "path",
            "svg.hashsalt": "bond-research-2026-11-03",
        }
    )
    args.output.mkdir(parents=True, exist_ok=True)
    dimensions(args.output)
    pipeline(args.output)
    replay(args.output)
    print(f"已生成 3 张 PNG 和 3 张 SVG；字体：{selected}")


if __name__ == "__main__":
    main()
