"""班级视图：把多个学生文件聚合成 知识点 × 错因 热力图，并给出教研可直接讨论的修订建议。

设计选择：没有共享数据库。一个学生一份 JSON，班级就是一个文件夹。
好处是零部署、学生数据天然隔离、丢一份不影响其他人；代价是不能实时多端同步——
对“每周一次教研会看一眼”这个使用频率来说，这是对的取舍。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from . import store
from .model import STATUS_ARCHIVED, Mistake
from .taxonomy import Taxonomy

MIN_COUNT = 3  # 低于这个数量的知识点不出建议，避免拿噪声当结论
DOMINANT_SHARE = 0.4  # 某错因占该知识点 40% 以上才算“主导错因”

# 主导错因 → 教研动作。这是产品的核心翻译：把学生的错，翻成讲义和课堂该改什么。
ACTIONS = {
    "C1": "讲义该节补概念例题或前置复习页；课前加 3 分钟概念检查",
    "C2": "补一张方法总结卡（信号词 → 方法）放在该节末尾；课上多讲“怎么想到的”",
    "C3": "不是讲义问题；课堂加限时小练，重做一律限时",
    "C4": "先检查该节题干表述是否有歧义；课堂强调圈条件、复述题意",
    "C5": "检查该节题量与难度排布；考虑拆题或给分步提示",
    "C6": "增加标准答案书写示范与评分点讲解",
}


def load_class(source: Path | str | list) -> list[dict]:
    """接收文件夹或文件列表。文件夹下取所有 *.json，跳过备份目录与临时文件。"""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_dir():
            paths = sorted(x for x in p.glob("*.json") if not x.name.endswith(".tmp"))
        else:
            paths = [p]
    else:
        paths = [Path(x) for x in source]
    if not paths:
        raise store.StoreError("没有找到任何学生数据文件")
    return [store.load(x) for x in paths]


def heatmap(docs: list[dict]) -> dict:
    matrix: dict[str, Counter] = defaultdict(Counter)
    fails: dict[str, Counter] = defaultdict(Counter)
    attention: list[dict] = []
    total = 0
    for doc in docs:
        name = doc["student"]["name"]
        for raw in doc["mistakes"]:
            m = Mistake.from_dict(raw)
            if m.status == STATUS_ARCHIVED:
                continue
            total += 1
            matrix[m.topic][m.cause] += 1
            fails[m.topic][m.cause] += sum(1 for a in m.attempts if not a.passed)
            if m.attention:
                attention.append(
                    {"student": name, "id": m.id, "topic": m.topic, "cause": m.cause,
                     "problem": m.problem, "fail_count": m.fail_count}
                )
    topics = sorted(matrix, key=lambda t: (-sum(matrix[t].values()), t))
    causes = sorted({c for t in matrix for c in matrix[t]})
    return {
        "students": len(docs),
        "mistakes": total,
        "topics": topics,
        "causes": causes,
        "matrix": {t: dict(matrix[t]) for t in topics},
        "fail_matrix": {t: dict(fails[t]) for t in topics},
        "cause_totals": dict(sum((matrix[t] for t in topics), Counter())),
        "attention": sorted(attention, key=lambda a: (-a["fail_count"], a["student"], a["id"])),
    }


def suggestions(heat: dict, taxonomy: Taxonomy, min_count: int = MIN_COUNT,
                share: float = DOMINANT_SHARE) -> list[dict]:
    out = []
    for topic in heat["topics"]:
        row = heat["matrix"][topic]
        n = sum(row.values())
        if n < min_count:
            continue
        code, k = max(row.items(), key=lambda kv: (kv[1], kv[0]))
        if k / n < share:
            continue
        out.append(
            {"topic": topic, "count": n, "cause": code, "cause_name": taxonomy.get(code).name,
             "share": k / n, "action": ACTIONS.get(code, "与教研讨论")}
        )
    return out


def render_heatmap_md(heat: dict, sugg: list[dict], taxonomy: Taxonomy) -> str:
    lines = [
        f"# 班级错因热力图（{heat['students']} 名学生，{heat['mistakes']} 道在练/已闭环错题）",
        "",
        "数字 = 错题数（括号内 = 重做失败次数）。只统计未归档错题，错因按当前归因。",
        "",
    ]
    causes = heat["causes"]
    head = "| 知识点 | 合计 | " + " | ".join(f"{c} {taxonomy.get(c).name}" for c in causes) + " |"
    lines.append(head)
    lines.append("|" + "---|" * (len(causes) + 2))
    for t in heat["topics"]:
        row, frow = heat["matrix"][t], heat["fail_matrix"][t]
        cells = [f"{row.get(c, 0)} ({frow.get(c, 0)})" if row.get(c) else "·" for c in causes]
        lines.append(f"| {t} | {sum(row.values())} | " + " | ".join(cells) + " |")
    lines += ["", "## 教研建议（主导错因 ≥ 40% 且该知识点 ≥ 3 道）", ""]
    if not sugg:
        lines.append("- 暂无达到阈值的知识点。")
    for s in sugg:
        lines.append(
            f"- **{s['topic']}**（{s['count']} 道，{s['cause']} {s['cause_name']} 占 {round(s['share'] * 100)}%）→ {s['action']}"
        )
    lines += ["", "## 老师本周要看的题（累计两次做错）", ""]
    if not heat["attention"]:
        lines.append("- 无。")
    for a in heat["attention"]:
        lines.append(f"- {a['student']} · {a['topic']} · {a['problem'][:30]}（{a['cause']}，错 {a['fail_count']} 次）")
    return "\n".join(lines) + "\n"
