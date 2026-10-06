from datetime import date

import pytest

from redo_right.cli import main
from redo_right.service import Service, ServiceError


class Clock:
    def __init__(self, d: date):
        self.d = d

    def __call__(self) -> date:
        return self.d


def make(tmp_path, start=date(2026, 10, 6)):
    clock = Clock(start)
    ids = iter(f"m{i:03d}" for i in range(1, 1000))
    svc = Service(tmp_path / "d.json", today_fn=clock, id_fn=lambda: next(ids))
    svc.init("小明", "高一")
    return svc, clock


def test_add_sets_due_tomorrow_and_records_first_attribution(tmp_path):
    svc, _ = make(tmp_path)
    m = svc.add("数列-递推", "C2", "讲义 L3 例 4", "讲义", "没想到构造等比")
    assert m.id == "m001" and m.due == "2026-10-07" and m.status == "open"
    assert m.cause_history[0].to_code == "C2" and m.cause_history[0].from_code is None


def test_add_validates_inputs(tmp_path):
    svc, _ = make(tmp_path)
    with pytest.raises(Exception, match="未知错因"):
        svc.add("t", "C9", "p")
    with pytest.raises(ServiceError, match="知识点"):
        svc.add("  ", "C1", "p")
    with pytest.raises(ServiceError, match="题目引用"):
        svc.add("t", "C1", " ")
    with pytest.raises(ServiceError, match="来源"):
        svc.add("t", "C1", "p", source="微信")


def test_due_only_shows_open_and_due(tmp_path):
    svc, clock = make(tmp_path)
    a = svc.add("t1", "C1", "p1")
    clock.d = date(2026, 10, 7)
    b = svc.add("t2", "C3", "p2")  # due 10-08
    assert [m.id for m in svc.due()] == [a.id]
    clock.d = date(2026, 10, 8)
    assert [m.id for m in svc.due()] == [a.id, b.id]  # 过期的排前面


def test_full_loop_closes_after_three_passes(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    for d in (date(2026, 10, 7), date(2026, 10, 14), date(2026, 11, 4)):
        clock.d = d
        m = svc.redo(m.id, True)
    assert m.status == "closed" and m.due is None and len(m.attempts) == 3
    assert svc.due() == []
    with pytest.raises(ServiceError, match="不能重做"):
        svc.redo(m.id, True)


def test_redo_rules(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    with pytest.raises(ServiceError, match="还没到重做日"):
        svc.redo(m.id, True)
    clock.d = date(2026, 10, 7)
    svc.redo(m.id, False, seconds=300, note="又忘了条件")
    with pytest.raises(ServiceError, match="一天只算一次"):
        svc.redo(m.id, True)
    with pytest.raises(ServiceError, match="找不到"):
        svc.redo("zzz", True)
    clock.d = date(2026, 10, 8)
    with pytest.raises(ServiceError, match="不能为负"):
        svc.redo(m.id, True, seconds=-1)
    m2 = svc.redo(m.id, True)
    assert m2.fail_count == 1 and m2.stage == 1 and m2.attempts[0].note == "又忘了条件"


def test_archive_requires_reason_and_removes_from_due(tmp_path):
    svc, clock = make(tmp_path)
    m = svc.add("t", "C1", "p")
    with pytest.raises(ServiceError, match="原因"):
        svc.archive(m.id, "  ")
    svc.archive(m.id, "超纲")
    clock.d = date(2026, 10, 7)
    assert svc.due() == []
    assert svc.list("archived")[0].note.endswith("超纲")


def test_data_survives_reload(tmp_path):
    svc, _ = make(tmp_path)
    svc.add("t", "C4", "p", note="看漏了偶数")
    again = Service(tmp_path / "d.json")
    assert again.list()[0].note == "看漏了偶数"


# ---------- CLI ----------

def test_cli_end_to_end(tmp_path, capsys):
    f = str(tmp_path / "d.json")
    assert main(["--file", f, "init", "--student", "小明", "--grade", "高一"]) == 0
    assert main(["--file", f, "--today", "2026-10-06", "add", "--topic", "函数-单调性", "--cause", "C4",
                 "--problem", "作业 3 第 7 题"]) == 0
    out = capsys.readouterr().out
    assert "下次重做 2026-10-07" in out and "圈出题干" in out
    assert main(["--file", f, "--today", "2026-10-06", "due"]) == 0
    assert "没有到期" in capsys.readouterr().out
    assert main(["--file", f, "--today", "2026-10-07", "due"]) == 0
    out = capsys.readouterr().out
    mid = out.split()[0]
    assert main(["--file", f, "--today", "2026-10-07", "redo", mid, "--pass"]) == 0
    assert "第 2/3 次重做" in capsys.readouterr().out
    assert main(["--file", f, "--today", "2026-10-07", "redo", mid, "--pass"]) == 1
    assert "一天只算一次" in capsys.readouterr().err
    assert main(["--file", f, "list"]) == 0
    assert mid in capsys.readouterr().out
    assert main(["causes"]) == 0
    assert "C1  概念不清" in capsys.readouterr().out


def test_cli_init_twice_fails(tmp_path, capsys):
    f = str(tmp_path / "d.json")
    assert main(["--file", f, "init", "--student", "a", "--grade", "b"]) == 0
    assert main(["--file", f, "init", "--student", "a", "--grade", "b"]) == 1
    assert "已存在" in capsys.readouterr().err


def test_cli_bad_ladder_argument(tmp_path):
    with pytest.raises(SystemExit):
        main(["--file", str(tmp_path / "d.json"), "init", "--student", "a", "--grade", "b", "--ladder", "x"])
