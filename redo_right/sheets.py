"""重练单（物料）与物料清单。

重练单是本产品唯一的“纸质物料”：每周按学生打印，把到期的题和“重做前先做的一件事”印在一起，
学生在纸上做完，回到工具里只记对/错/用时。
物料清单回答供应链和打印环节的三个问题：印谁的、印几页、几张纸。
"""

from __future__ import annotations

import html
import math
from datetime import date

from .model import STATUS_OPEN, Mistake
from .taxonomy import Taxonomy

ITEMS_PER_PAGE = 3  # 竞赛题需要作答空间，一页三道


def sheet_items(doc: dict, on: date) -> dict:
    redo, retag = [], []
    for raw in doc["mistakes"]:
        m = Mistake.from_dict(raw)
        if m.status != STATUS_OPEN:
            continue
        if m.needs_retag:
            retag.append(m)
        elif m.due and date.fromisoformat(m.due) <= on:
            redo.append(m)
    redo.sort(key=lambda m: (m.due or "", m.created, m.id))
    retag.sort(key=lambda m: (m.created, m.id))
    return {"redo": redo, "retag": retag}


def pages_for(n_items: int) -> int:
    return math.ceil(n_items / ITEMS_PER_PAGE) if n_items else 0


_CSS = """
@page { size: A4; margin: 14mm; }
body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; font-size: 11pt; color: #111; }
h1 { font-size: 15pt; margin: 0 0 4mm; }
.meta { color: #555; font-size: 9.5pt; margin-bottom: 6mm; }
.item { border: 1px solid #999; padding: 3mm 4mm; margin-bottom: 5mm; page-break-inside: avoid; }
.item .head { display: flex; justify-content: space-between; font-weight: 600; }
.item .step { font-size: 9.5pt; color: #333; margin: 1.5mm 0; }
.work { min-height: 58mm; border-top: 1px dashed #bbb; margin-top: 2mm; }
.result { font-size: 9.5pt; margin-top: 2mm; }
.retag .work { min-height: 22mm; }
.student + .student { page-break-before: always; }
.footer { font-size: 9pt; color: #666; margin-top: 4mm; }
"""


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _item_html(m: Mistake, taxonomy: Taxonomy, retag: bool) -> str:
    c = taxonomy.get(m.cause)
    timed = c.redo_mode == "timed"
    if retag:
        prompt = "这道题已经两次做错。先写一句：这次想清楚了，错在哪一类？（概念/思路/计算/审题/时间/表达）为什么？"
        result = "重新归因：C___ &nbsp; 理由见上。回到工具里执行 retag 后再重做。"
    else:
        prompt = f"重做前先：{c.next_step}"
        result = "结果：□ 做对 &nbsp; □ 又错 &nbsp; 用时 ______ 秒" + ("（本题限时）" if timed else "")
    return (
        f'<div class="item{" retag" if retag else ""}">'
        f'<div class="head"><span>{_esc(m.topic)} · {_esc(m.problem)}</span>'
        f'<span>{_esc(m.id)} · {_esc(c.code)} {_esc(c.name)} · 第 {m.stage + 1} 次重做</span></div>'
        f'<div class="step">{_esc(prompt)}</div>'
        f'<div class="work"></div>'
        f'<div class="result">{result}</div>'
        f"</div>"
    )


def render_student_sheet(doc: dict, on: date, taxonomy: Taxonomy) -> str:
    items = sheet_items(doc, on)
    name, grade = doc["student"]["name"], doc["student"]["grade"]
    parts = [
        f'<section class="student"><h1>{_esc(name)} · 错题重练单</h1>',
        f'<div class="meta">{_esc(grade)} · 打印日期 {on.isoformat()} · 到期重做 {len(items["redo"])} 道 · 待归因 {len(items["retag"])} 道</div>',
    ]
    parts += [_item_html(m, taxonomy, retag=False) for m in items["redo"]]
    parts += [_item_html(m, taxonomy, retag=True) for m in items["retag"]]
    if not items["redo"] and not items["retag"]:
        parts.append("<p>今天没有到期的错题。</p>")
    parts.append('<div class="footer">做完后回到工具：redo &lt;id&gt; --pass / --fail --seconds N。只记结果，不抄题。</div></section>')
    return "\n".join(parts)


def wrap_html(body: str, title: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )


def bill_of_materials(docs: list[dict], on: date) -> dict:
    rows, skipped = [], []
    for doc in docs:
        items = sheet_items(doc, on)
        n = len(items["redo"]) + len(items["retag"])
        name = doc["student"]["name"]
        if n == 0:
            skipped.append(name)
            continue
        rows.append({"student": name, "items": n, "pages": pages_for(n)})
    total_pages = sum(r["pages"] for r in rows)
    return {
        "on": on.isoformat(),
        "students_total": len(docs),
        "students_to_print": len(rows),
        "rows": sorted(rows, key=lambda r: r["student"]),
        "skipped": sorted(skipped),
        "total_pages": total_pages,
        "sheets_double_sided": math.ceil(total_pages / 2),
        "items_per_page": ITEMS_PER_PAGE,
    }


def render_bom_md(bom: dict) -> str:
    lines = [
        f"# 重练单物料清单 · {bom['on']}",
        "",
        f"- 班级 {bom['students_total']} 人，需打印 {bom['students_to_print']} 人；共 {bom['total_pages']} 页，双面 {bom['sheets_double_sided']} 张（每页 {bom['items_per_page']} 道）",
        f"- 本次无需打印：{'、'.join(bom['skipped']) if bom['skipped'] else '无'}",
        "",
        "| 学生 | 题数 | 页数 |",
        "|---|---|---|",
    ]
    lines += [f"| {r['student']} | {r['items']} | {r['pages']} |" for r in bom["rows"]]
    lines += ["", "核对：发放前按学生姓名与页数逐份核对；缺页/错发在工具里不留痕，必须在发放时拦住。"]
    return "\n".join(lines) + "\n"
