"""v0.2：二次错规则、重新归因、学生周报、CSV 导出。"""

from datetime import date

import pytest

from redo_right.cli import main
from redo_right.report import export_csv, render_student_report, weekly_summary
from redo_right.service import RETAG_THRESHOLD, Service, ServiceError


class Clock:
    def __init__(self, d):
        self.d = d

    def __call__(self):
        return self.d


def make(tmp_path):
    clock = Clock(date(2026, 10, 6))
    ids = iter(f"m{i:03d}" for i in range(1, 1000))
    svc = Service(tmp_path / "d.json", today_fn=clock, id_fn=lambda: next(ids))
    svc.init("小明", "高一")
    return svc, clock


def fail_twice(svc, clock, m):
    clock.d = date(2026, 10, 7)
    svc.redo(m.id, False)
    clock.d = date(2026, 10, 8)
    return svc.redo(m.id, False)


def test_threshold_is_two():
    assert RETAG_THRESHOLD == 2


def test_second_fail_flags_and_blocks_redo(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("导数-极值", "C3", "模考 2 第 18 题", "模考")
    m = fail_twice(svc, clock, m)
    assert m.needs_retag and m.attention and m.fail_count == 2
    clock.d = date(2026, 10, 9)
    with pytest.raises(ServiceError, match="先用 retag"):
        svc.redo(m.id, True)
    assert svc.due() == []  # 待重做列表不再催这道题，直到重新归因  (v0.2 规则)


def test_first_fail_does_not_flag(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    clock.d = date(2026, 10, 7)
    m = svc.redo(m.id, False)
    assert not m.needs_retag and not m.attention


def test_retag_clears_flag_records_history_and_reopens_redo(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C3", "p", note="算错了")
    fail_twice(svc, clock, m)
    with pytest.raises(ServiceError, match="理由"):
        svc.retag(m.id, "C2", " ")
    with pytest.raises(Exception, match="未知错因"):
        svc.retag(m.id, "C9", "x")
    m = svc.retag(m.id, "C2", "其实不是算错，是没想到换元", by="student")
    assert m.cause == "C2" and not m.needs_retag
    assert m.attention  # 老师关注不随学生重标清除
    assert [c.to_code for c in m.cause_history] == ["C3", "C2"]
    assert m.cause_history[-1].from_code == "C3" and m.cause_history[-1].by == "student"
    clock.d = date(2026, 10, 9)
    m = svc.redo(m.id, True)
    assert m.stage == 1


def test_retag_same_code_allowed_with_reason(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C3", "p")
    fail_twice(svc, clock, m)
    m = svc.retag(m.id, "C3", "确认还是计算问题，这次要分步验算")
    assert m.cause == "C3" and len(m.cause_history) == 2


def test_clear_attention_requires_note(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C3", "p")
    fail_twice(svc, clock, m)
    with pytest.raises(ServiceError, match="处理记录"):
        svc.clear_attention(m.id, "")
    m = svc.clear_attention(m.id, "课上讲过换元法")
    assert not m.attention and "课上讲过换元法" in m.note


def test_retag_closed_mistake_rejected(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    for d in (date(2026, 10, 7), date(2026, 10, 14), date(2026, 11, 4)):
        clock.d = d
        svc.redo(m.id, True)
    with pytest.raises(ServiceError, match="不需要重新归因"):
        svc.retag(m.id, "C2", "x")


# ---------- 周报 ----------

def scenario(tmp_path):
    svc, clock = make(tmp_path)
    a = svc.add("数列", "C2", "讲义 L3 例 4")  # 10-06
    b = svc.add("函数", "C4", "作业 3 第 7 题")
    clock.d = date(2026, 10, 7)
    svc.redo(a.id, True)
    svc.redo(b.id, False)
    c = svc.add("导数", "C3", "模考 2 第 18 题", "模考")
    clock.d = date(2026, 10, 8)
    svc.redo(b.id, False)  # b 二次错
    svc.redo(c.id, True)
    clock.d = date(2026, 10, 12)
    return svc, clock, a, b, c


def test_weekly_summary_numbers(tmp_path):
    svc, clock, a, b, c = scenario(tmp_path)
    s = weekly_summary(svc.document(), date(2026, 10, 12))
    assert (s["week_start"], s["week_end"]) == ("2026-10-06", "2026-10-12")
    assert s["added"] == 3 and s["attempts"] == 4 and s["passed"] == 2 and s["failed"] == 2
    assert s["active_days"] == 2
    assert s["closed_in_week"] == 0 and s["open_total"] == 3 and s["closure_rate"] == 0
    assert s["by_cause_failed"] == {"C4": 2}
    assert s["needs_retag_ids"] == [b.id] and s["attention_ids"] == [b.id]
    assert s["waiting_ids"] == []  # a 到期 10-14、c 到期 10-15，都在周末之后
def test_waiting_means_due_on_or_before_week_end(tmp_path):
    svc, clock, a, b, c = scenario(tmp_path)
    # a: 10-07 通过 → 10-14；c: 10-08 通过 → 10-15。到 10-12 都没到期。
    s = weekly_summary(svc.document(), date(2026, 10, 12))
    assert s["waiting_ids"] == []
    s2 = weekly_summary(svc.document(), date(2026, 10, 15))
    assert s2["waiting_ids"] == [a.id, c.id]


def test_closure_rate_and_closed_in_week(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    svc.add("t2", "C1", "p2")
    for d in (date(2026, 10, 7), date(2026, 10, 14), date(2026, 11, 4)):
        clock.d = d
        svc.redo(m.id, True)
    s = weekly_summary(svc.document(), date(2026, 11, 8))
    assert s["closed_in_week"] == 1 and s["closure_rate"] == 0.5


def test_archived_excluded_from_rates(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    svc.archive(m.id, "超纲")
    s = weekly_summary(svc.document(), date(2026, 10, 12))
    assert s["closure_rate"] is None and s["open_total"] == 0


def test_render_student_report_mentions_key_facts(tmp_path):
    svc, clock, a, b, c = scenario(tmp_path)
    doc = svc.document()
    text = render_student_report(weekly_summary(doc, date(2026, 10, 12)), svc.taxonomy, doc)
    assert "小明 的本周错题周报（2026-10-06 ~ 2026-10-12）" in text
    assert "新记错题 3 道；重做 4 次（做对 2，又错 2）" in text
    assert "C4 审题错误：又错 2 次" in text and "圈出题干" in text
    assert "先重新归因再重做" in text and b.id in text
    assert "作业 3 第 7 题" in text


def test_export_csv(tmp_path):
    svc, clock, a, b, c = scenario(tmp_path)
    text = export_csv(svc.document(), svc.taxonomy)
    lines = text.strip().splitlines()
    assert lines[0].startswith("id,created,source,topic,cause,cause_name,status")
    assert len(lines) == 4
    row_b = next(l for l in lines if l.startswith(b.id))
    assert ",C4,审题错误,open,0,2," in row_b and row_b.endswith(",1,1,作业 3 第 7 题,")


def test_cli_retag_report_export(tmp_path, capsys):
    f = str(tmp_path / "d.json")
    main(["--file", f, "init", "--student", "小明", "--grade", "高一"])
    main(["--file", f, "--today", "2026-10-06", "add", "--topic", "t", "--cause", "C3", "--problem", "p"])
    mid = [m.id for m in Service(f).list()][0]
    main(["--file", f, "--today", "2026-10-07", "redo", mid, "--fail"])
    main(["--file", f, "--today", "2026-10-08", "redo", mid, "--fail"])
    capsys.readouterr()
    assert main(["--file", f, "--today", "2026-10-09", "redo", mid, "--pass"]) == 1
    assert "先用 retag" in capsys.readouterr().err
    assert main(["--file", f, "--today", "2026-10-09", "retag", mid, "--cause", "C2", "--reason", "没想到换元"]) == 0
    assert "C2 思路未想到" in capsys.readouterr().out
    assert main(["--file", f, "--today", "2026-10-09", "report"]) == 0
    assert "本周数字" in capsys.readouterr().out
    out = tmp_path / "r.md"
    assert main(["--file", f, "--today", "2026-10-09", "report", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 小明 的本周错题周报")
    csvp = tmp_path / "e.csv"
    assert main(["--file", f, "export", "--out", str(csvp)]) == 0
    assert csvp.read_text(encoding="utf-8-sig").splitlines()[0].startswith("id,")
    assert main(["--file", f, "--today", "2026-10-09", "attended", mid, "--note", "课上讲过"]) == 0
