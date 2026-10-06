"""报表：全部确定性计算，数字先算出来，措辞只是把数字念出来。

v0.2 只有学生周报与 CSV 导出。家长周报与教研热力图在后续版本。
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, timedelta

from .model import STATUS_CLOSED, STATUS_OPEN, Mistake
from .taxonomy import Taxonomy


def week_range(week_end: date) -> tuple[date, date]:
    return week_end - timedelta(days=6), week_end


def _in(start: date, end: date, iso: str) -> bool:
    return start <= date.fromisoformat(iso) <= end


def weekly_summary(doc: dict, week_end: date) -> dict:
    start, end = week_range(week_end)
    ms = [Mistake.from_dict(m) for m in doc["mistakes"]]
    attempts = [(m, a) for m in ms for a in m.attempts if _in(start, end, a.date)]
    passed = sum(1 for _, a in attempts if a.passed)
    open_ms = [m for m in ms if m.status == STATUS_OPEN]
    closed_ms = [m for m in ms if m.status == STATUS_CLOSED]
    closed_in_week = [
        m for m in closed_ms if m.attempts and _in(start, end, m.attempts[-1].date)
    ]
    waiting = sorted(
        (m for m in open_ms if m.due and date.fromisoformat(m.due) <= end and not m.needs_retag),
        key=lambda m: m.due or "",
    )
    needs_retag = [m for m in open_ms if m.needs_retag]
    tracked = len(open_ms) + len(closed_ms)
    return {
        "student": doc["student"],
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "added": sum(1 for m in ms if _in(start, end, m.created)),
        "attempts": len(attempts),
        "passed": passed,
        "failed": len(attempts) - passed,
        "active_days": len({a.date for _, a in attempts}),
        "closed_in_week": len(closed_in_week),
        "open_total": len(open_ms),
        "closed_total": len(closed_ms),
        "closure_rate": (len(closed_ms) / tracked) if tracked else None,
        "by_cause_open": dict(Counter(m.cause for m in open_ms)),
        "by_cause_failed": dict(Counter(m.cause for m, a in attempts if not a.passed)),
        "last_fail_by_cause": _last_fail_by_cause(attempts),
        "needs_retag_ids": [m.id for m in needs_retag],
        "waiting_ids": [m.id for m in waiting],
        "attention_ids": [m.id for m in open_ms if m.attention],
    }


def _last_fail_by_cause(attempts) -> dict[str, str]:
    out: dict[str, str] = {}
    for m, a in attempts:
        if not a.passed and a.date > out.get(m.cause, ""):
            out[m.cause] = a.date
    return out


def top_failed_cause(summary: dict) -> tuple[str, int] | None:
    """本周最常见的错因；平票时取最近一次失败的那一类，再平才按编码。"""
    counts = summary["by_cause_failed"]
    if not counts:
        return None
    last = summary.get("last_fail_by_cause", {})
    code = max(counts, key=lambda c: (counts[c], last.get(c, ""), c))
    return code, counts[code]


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


def render_student_report(summary: dict, taxonomy: Taxonomy, doc: dict) -> str:
    s = summary
    by_id = {m["id"]: m for m in doc["mistakes"]}
    lines = [
        f"# {s['student']['name']} 的本周错题周报（{s['week_start']} ~ {s['week_end']}）",
        "",
        "## 本周数字",
        f"- 新记错题 {s['added']} 道；重做 {s['attempts']} 次（做对 {s['passed']}，又错 {s['failed']}）；有重做的天数 {s['active_days']}/7",
        f"- 本周闭环 {s['closed_in_week']} 道；累计闭环率 {_pct(s['closure_rate'])}（闭环 {s['closed_total']} / 在练 {s['open_total']}）",
    ]
    top = top_failed_cause(s)
    if top:
        c = taxonomy.get(top[0])
        lines += [
            "",
            "## 本周最常见的错因",
            f"- {c.code} {c.name}：又错 {top[1]} 次。下次重做前先：{c.next_step}",
        ]
    if s["by_cause_open"]:
        lines += ["", "## 在练错题的错因分布"]
        for code, n in sorted(s["by_cause_open"].items()):
            lines.append(f"- {code} {taxonomy.get(code).name}：{n} 道")
    lines += ["", "## 待办"]
    if s["needs_retag_ids"]:
        lines.append("- **先重新归因再重做**（累计两次做错）：")
        for i in s["needs_retag_ids"]:
            m = by_id[i]
            lines.append(f"  - {i} {m['topic']} · {m['problem'][:30]}")
    if s["waiting_ids"]:
        lines.append("- 到期待重做：")
        for i in s["waiting_ids"]:
            m = by_id[i]
            lines.append(f"  - {i} {m['topic']} · 到期 {m['due']} · {m['problem'][:30]}")
    if not s["needs_retag_ids"] and not s["waiting_ids"]:
        lines.append("- 没有积压，保持。")
    return "\n".join(lines) + "\n"


CSV_COLUMNS = (
    "id", "created", "source", "topic", "cause", "cause_name", "status", "stage",
    "fail_count", "due", "attempts", "needs_retag", "attention", "problem", "note",
)


def export_csv(doc: dict, taxonomy: Taxonomy) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    for raw in doc["mistakes"]:
        m = Mistake.from_dict(raw)
        w.writerow(
            {
                "id": m.id,
                "created": m.created,
                "source": m.source,
                "topic": m.topic,
                "cause": m.cause,
                "cause_name": taxonomy.get(m.cause).name if taxonomy.has(m.cause) else "",
                "status": m.status,
                "stage": m.stage,
                "fail_count": m.fail_count,
                "due": m.due or "",
                "attempts": len(m.attempts),
                "needs_retag": int(m.needs_retag),
                "attention": int(m.attention),
                "problem": m.problem,
                "note": m.note,
            }
        )
    return buf.getvalue()


# ---------- 家长周报 ----------
# 三个数字、一句错因、一件事。不出现题目内容，不出现“差/落后/粗心”这类评价词。

_PARENT_ACTION = {
    "C1": "重做前让孩子先用自己的话讲一遍这个知识点，讲得出来再动笔",
    "C2": "让孩子先说“这题的信号是什么、该用什么方法”，说完再做",
    "C3": "重做时请计时，并让孩子每步写出验算；不要在旁边提示",
    "C4": "提醒孩子做题前把题干条件逐个圈出来",
    "C5": "重做一律计时，和上次用时比，不比对错",
    "C6": "做完后对照标准答案，看每一步是否写全，而不只看结果",
}


def parent_digest(summary: dict, taxonomy: Taxonomy) -> str:
    s = summary
    name = s["student"]["name"]
    lines = [f"{name} 本周错题小结（{s['week_start']} ~ {s['week_end']}）", ""]
    lines.append(f"1. 本周重做 {s['attempts']} 次，做对 {s['passed']} 次，有 {s['active_days']} 天做了重做。")
    lines.append(f"2. 累计 {s['closed_total']} 道题已经三次做对（闭环），{s['open_total']} 道还在练。")
    top = top_failed_cause(s)
    if top:
        code, n = top
        c = taxonomy.get(code)
        lines.append(f"3. 本周最常见的错因：{c.name}（{n} 次）。老师对这类错的练法是：{c.next_step}")
        action = _PARENT_ACTION.get(code, "照常安排重做时间即可")
    else:
        lines.append("3. 本周没有重做失败的记录。")
        action = "照常安排重做时间即可"
    if s["needs_retag_ids"]:
        lines.append(
            f"4. 有 {len(s['needs_retag_ids'])} 道题两次没做对，老师已收到提醒；孩子需要先想清楚错因再重做，这一步比多做题更重要。"
        )
    if s["attempts"] == 0:
        action = "帮孩子固定一个每周两次、每次 20 分钟的重做时间，比多买一本题更有用"
    elif s["active_days"] < 2:
        action = "把重做放进固定时段（比如周三、周日晚），一次 20 分钟"
    lines += ["", f"您这周能帮的一件事：{action}。"]
    return "\n".join(lines) + "\n"
