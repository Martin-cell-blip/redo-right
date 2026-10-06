"""v0.3：班级热力图与教研建议、家长周报、重练单、物料清单。"""

from datetime import date
from pathlib import Path

from redo_right.classroom import ACTIONS, heatmap, load_class, render_heatmap_md, suggestions
from redo_right.cli import main
from redo_right.report import parent_digest, weekly_summary
from redo_right.service import Service
from redo_right.sheets import (
    ITEMS_PER_PAGE,
    bill_of_materials,
    pages_for,
    render_bom_md,
    render_student_sheet,
    sheet_items,
    wrap_html,
)
from redo_right.taxonomy import load_taxonomy

TAX = load_taxonomy()


class Clock:
    def __init__(self, d):
        self.d = d

    def __call__(self):
        return self.d


def student(dir_: Path, name: str, start=date(2026, 10, 6)):
    clock = Clock(start)
    ids = iter(f"{name}{i:02d}" for i in range(1, 100))
    svc = Service(dir_ / f"{name}.json", today_fn=clock, id_fn=lambda: next(ids))
    svc.init(name, "高一")
    return svc, clock


def build_class(tmp_path):
    d = tmp_path / "cls"
    d.mkdir()
    a, ca = student(d, "甲")
    b, cb = student(d, "乙")
    c, cc = student(d, "丙")
    # 数列：甲 2 道 C2，乙 1 道 C2，丙 1 道 C3 → 4 道，C2 占 75% → 建议
    a.add("数列", "C2", "讲义 L3 例 4")
    a.add("数列", "C2", "作业 2 第 5 题")
    b.add("数列", "C2", "作业 2 第 5 题")
    c.add("数列", "C3", "作业 2 第 6 题")
    # 函数：只有 2 道 → 不够阈值
    a.add("函数", "C1", "作业 3 第 7 题")
    b.add("函数", "C4", "作业 3 第 8 题")
    # 几何：3 道但分散 C3/C4/C5 各 1 → 无主导
    a.add("几何", "C3", "模考 1 第 18 题", "模考")
    b.add("几何", "C4", "模考 1 第 18 题", "模考")
    c.add("几何", "C5", "模考 1 第 18 题", "模考")
    # 乙把数列那道做错两次 → attention
    cb.d = date(2026, 10, 7)
    b.redo("乙01", False)
    cb.d = date(2026, 10, 8)
    b.redo("乙01", False)
    # 甲一道归档，不应计入
    a.archive("甲04", "超纲")
    # 丙一道通过一次
    cc.d = date(2026, 10, 7)
    c.redo("丙01", True)
    return d


def test_load_class_from_dir_ignores_backups(tmp_path):
    d = build_class(tmp_path)
    assert (d / "乙.json.bak").is_dir()  # 乙写过多次，有备份目录
    docs = load_class(d)
    assert [x["student"]["name"] for x in docs] == ["丙", "乙", "甲"] or len(docs) == 3


def test_heatmap_counts_exclude_archived_and_track_attention(tmp_path):
    heat = heatmap(load_class(build_class(tmp_path)))
    assert heat["students"] == 3 and heat["mistakes"] == 8  # 9 道减 1 道归档
    assert heat["matrix"]["数列"] == {"C2": 3, "C3": 1}
    assert heat["fail_matrix"]["数列"]["C2"] == 2
    assert heat["topics"][0] == "数列"  # 按数量排序
    assert heat["cause_totals"]["C2"] == 3
    assert [a["student"] for a in heat["attention"]] == ["乙"]
    assert heat["attention"][0]["fail_count"] == 2


def test_suggestions_threshold_and_dominance(tmp_path):
    heat = heatmap(load_class(build_class(tmp_path)))
    sugg = suggestions(heat, TAX)
    assert [s["topic"] for s in sugg] == ["数列"]
    assert sugg[0]["cause"] == "C2" and sugg[0]["share"] == 0.75
    assert sugg[0]["action"] == ACTIONS["C2"]
    assert len(suggestions(heat, TAX, min_count=2)) == 3  # 函数、几何各 2 道，50% 也过 40% 线——这就是为什么默认阈值是 3


def test_render_heatmap_md(tmp_path):
    heat = heatmap(load_class(build_class(tmp_path)))
    text = render_heatmap_md(heat, suggestions(heat, TAX), TAX)
    assert "3 名学生，8 道" in text
    assert "| 数列 | 4 | " in text and "3 (2)" in text
    assert "**数列**（4 道，C2 思路未想到 占 75%）" in text and "方法总结卡" in text
    assert "乙 · 数列 · 作业 2 第 5 题（C2，错 2 次）" in text


# ---------- 家长周报 ----------

def test_parent_digest_has_numbers_action_and_no_problem_text(tmp_path):
    d = build_class(tmp_path)
    doc = Service(d / "乙.json").document()
    s = weekly_summary(doc, date(2026, 10, 12))
    text = parent_digest(s, TAX)
    assert text.startswith("乙 本周错题小结（2026-10-06 ~ 2026-10-12）")
    assert "本周重做 2 次，做对 0 次，有 2 天做了重做" in text
    assert "最常见的错因：思路未想到（2 次）" in text
    assert "有 1 道题两次没做对，老师已收到提醒" in text
    assert "您这周能帮的一件事" in text
    for forbidden in ("作业 2 第 5 题", "模考 1 第 18 题", "粗心", "落后"):
        assert forbidden not in text


def test_parent_digest_zero_activity_action(tmp_path):
    d = build_class(tmp_path)
    doc = Service(d / "甲.json").document()
    text = parent_digest(weekly_summary(doc, date(2026, 10, 12)), TAX)
    assert "本周重做 0 次" in text and "固定一个每周两次、每次 20 分钟的重做时间" in text


# ---------- 重练单 / 物料 ----------

def test_sheet_items_split_redo_and_retag(tmp_path):
    d = build_class(tmp_path)
    items = sheet_items(Service(d / "乙.json").document(), date(2026, 10, 9))
    assert [m.id for m in items["retag"]] == ["乙01"]
    assert [m.id for m in items["redo"]] == ["乙02", "乙03"]  # 乙01 不在重做栏
    items_early = sheet_items(Service(d / "甲.json").document(), date(2026, 10, 6))
    assert items_early == {"redo": [], "retag": []}  # 当天记的题明天才到期


def test_render_student_sheet_html(tmp_path):
    d = build_class(tmp_path)
    html = wrap_html(render_student_sheet(Service(d / "乙.json").document(), date(2026, 10, 9), TAX), "t")
    assert html.startswith("<!doctype html>") and "@page { size: A4" in html
    assert "乙 · 错题重练单" in html and "到期重做 2 道 · 待归因 1 道" in html
    assert html.count('class="item') == 3 and html.count('class="item retag"') == 1
    assert "重做前先：圈出题干每个条件和所求" in html  # 乙02/乙03 是 C4；乙01(C2) 处于归因模式不显示重做提示
    assert "这道题已经两次做错" in html
    # 甲的归档题不出现
    html_a = render_student_sheet(Service(d / "甲.json").document(), date(2026, 10, 9), TAX)
    assert "甲04" not in html_a and html_a.count('class="item') == 3


def test_sheet_escapes_html(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    svc, clock = student(d, "丁")
    svc.add("t", "C1", "<script>alert(1)</script>")
    clock.d = date(2026, 10, 7)
    html = render_student_sheet(svc.document(), clock.d, TAX)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_pages_and_bom(tmp_path):
    assert ITEMS_PER_PAGE == 3
    assert [pages_for(n) for n in (0, 1, 3, 4, 7)] == [0, 1, 1, 2, 3]
    d = build_class(tmp_path)
    bom = bill_of_materials(load_class(d), date(2026, 10, 9))
    rows = {r["student"]: r for r in bom["rows"]}
    assert rows["甲"] == {"student": "甲", "items": 3, "pages": 1}
    assert rows["乙"] == {"student": "乙", "items": 3, "pages": 1}
    assert rows["丙"] == {"student": "丙", "items": 1, "pages": 1}  # 丙01 通过后到期 10-14，只剩丙02
    assert bom["total_pages"] == 3 and bom["sheets_double_sided"] == 2 and bom["skipped"] == []
    bom0 = bill_of_materials(load_class(d), date(2026, 10, 6))
    # 打印日期早于到期日：没有到期题；但待归因标记是当前状态，乙 仍要印一页归因单
    assert bom0["students_to_print"] == 1 and bom0["skipped"] == ["丙", "甲"] and bom0["total_pages"] == 1
    md = render_bom_md(bom)
    assert "需打印 3 人；共 3 页，双面 2 张" in md and "| 丙 | 1 | 1 |" in md


def test_cli_parent_sheet_class(tmp_path, capsys):
    d = build_class(tmp_path)
    f = str(d / "乙.json")
    assert main(["--file", f, "--today", "2026-10-12", "parent"]) == 0
    assert "您这周能帮的一件事" in capsys.readouterr().out
    out = tmp_path / "sheet.html"
    assert main(["--file", f, "--today", "2026-10-09", "sheet", "--out", str(out)]) == 0
    assert "错题重练单" in out.read_text(encoding="utf-8")
    assert main(["class", "--dir", str(d), "heatmap"]) == 0
    assert "班级错因热力图" in capsys.readouterr().out
    hm = tmp_path / "heat.md"
    assert main(["class", "--dir", str(d), "heatmap", "--out", str(hm)]) == 0
    assert hm.read_text(encoding="utf-8").startswith("# 班级错因热力图")
    od = tmp_path / "sheets"
    assert main(["class", "--dir", str(d), "sheets", "--on", "2026-10-09", "--out-dir", str(od)]) == 0
    assert "已生成 3 份重练单，共 3 页" in capsys.readouterr().out
    assert (od / "重练单_乙_2026-10-09.html").exists() and (od / "物料清单_2026-10-09.md").exists()
    assert main(["class", "--dir", str(tmp_path / "empty"), "heatmap"]) == 1
