"""v0.4：AI 合同、回退、采纳率日志。全部离线（call_fn 注入），不碰网络。"""

import json
from datetime import date

import pytest

from redo_right import ai
from redo_right.cli import main
from redo_right.report import parent_digest, weekly_summary
from redo_right.service import Service, ServiceError
from redo_right.taxonomy import load_taxonomy

TAX = load_taxonomy()
NOTE = "我当时没想到要换元，直接硬算，最后一步还算错了"
PROBLEM = "讲义 L3 例 4"


def ok_json(**kw):
    base = {"code": "C2", "evidence_quote": "没想到要换元", "question": "题干里哪个信号本该让你想到换元？", "confidence": "high"}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


class Fake:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0)


# ---------- 合同 ----------

def test_compliant_output_passes():
    sg = ai.suggest_cause(PROBLEM, NOTE, TAX, Fake(ok_json()))
    assert sg.code == "C2" and sg.source == "ai" and sg.violations == []
    assert sg.evidence_quote == "没想到要换元"


@pytest.mark.parametrize("bad, msg", [
    ({"code": "C9"}, "编码不在错因表"),
    ({"evidence_quote": "学生说他没想到换元法"}, "逐字"),
    ({"evidence_quote": "x" * 61}, "60 字"),
    ({"question": "你应该先换元再算。"}, "问号结尾"),
    ({"question": "设 t=x+1，答案是 3，对吗？"}, "解题内容"),
    ({"question": "你" * 61 + "？"}, "60 字"),
    ({"confidence": "sure"}, "confidence"),
])
def test_violations_detected(bad, msg):
    raw = json.loads(ok_json(**bad))
    v = ai.validate_suggestion(raw, NOTE, PROBLEM, TAX)
    assert any(msg in x for x in v), v


def test_retry_once_then_accept():
    fake = Fake(ok_json(code="C9"), ok_json())
    sg = ai.suggest_cause(PROBLEM, NOTE, TAX, fake)
    assert sg.code == "C2" and sg.source == "ai" and len(sg.violations) == 1
    assert len(fake.calls) == 2 and "违反合同" in fake.calls[1][-1]["content"]


def test_two_violations_fall_back_to_deterministic():
    fake = Fake(ok_json(question="你应该用换元。"), "not json at all")
    sg = ai.suggest_cause(PROBLEM, NOTE, TAX, fake)
    assert sg.source == "deterministic" and sg.code == "C2"  # 关键词“没想到”
    assert sg.evidence_quote == "没想到" and len(sg.violations) == 2


def test_no_key_no_callfn_is_deterministic(monkeypatch):
    monkeypatch.delenv("REDO_RIGHT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    sg = ai.suggest_cause(PROBLEM, "符号算错了", TAX)
    assert sg.source == "deterministic" and sg.code == "C3"


def test_deterministic_picks_earliest_keyword_and_handles_unknown():
    sg = ai.deterministic_suggestion("算错了，而且没想到换元", PROBLEM, TAX)
    assert sg.code == "C3"  # “算错”出现在前
    sg2 = ai.deterministic_suggestion("就是不会", PROBLEM, TAX)
    assert sg2.code is None and sg2.question.endswith("？")


def test_empty_note_never_calls_ai():
    fake = Fake(ok_json())
    sg = ai.suggest_cause(PROBLEM, "   ", TAX, fake)
    assert sg.source == "deterministic" and fake.calls == []


def test_markdown_fenced_json_is_parsed():
    sg = ai.suggest_cause(PROBLEM, NOTE, TAX, Fake("```json\n" + ok_json() + "\n```"))
    assert sg.source == "ai"


# ---------- 家长周报措辞 ----------

def make_summary(tmp_path):
    svc = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 6), id_fn=iter(["a1", "a2"]).__next__)
    svc.init("乙", "高一")
    svc.add("数列", "C2", "作业 2 第 5 题")
    svc.add("函数", "C4", "作业 3 第 8 题")
    svc2 = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 7))
    svc2.redo("a1", False)
    svc3 = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 8))
    svc3.redo("a1", False)
    s = weekly_summary(svc.document(), date(2026, 10, 12))
    return s, parent_digest(s, TAX)


def test_digest_numbers_must_exist(tmp_path):
    s, det = make_summary(tmp_path)
    assert ai.validate_digest(det, s) == []  # 确定性版本自己必须过合同
    assert any("数字 9" in v for v in ai.validate_digest("本周重做 9 次", s))
    assert any("评价词" in v for v in ai.validate_digest("孩子有点粗心", s))
    assert any("超过" in v for v in ai.validate_digest("好" * 400, s))


def test_phrase_digest_accepts_compliant_and_falls_back(tmp_path):
    s, det = make_summary(tmp_path)
    good = json.dumps({"text": "乙这周重做了 2 次，两次都还没做对，不过坚持了 2 天。有 1 道题两次没做对，老师会关注。您这周能帮的一件事：让孩子先说思路再动笔。"}, ensure_ascii=False)
    text, source, v = ai.phrase_parent_digest(det, s, Fake(good))
    assert source == "ai" and text.startswith("乙这周") and v == []
    bad = json.dumps({"text": "乙这周重做了 5 次，有点粗心。"}, ensure_ascii=False)
    text2, source2, v2 = ai.phrase_parent_digest(det, s, Fake(bad, bad))
    assert source2 == "deterministic" and text2 == det and len(v2) >= 2


# ---------- 采纳率日志 ----------

def test_log_and_resolve_suggestion(tmp_path):
    svc = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 6), id_fn=iter(["a1"]).__next__)
    svc.init("乙", "高一")
    sid = svc.log_suggestion("cause", "abc", {"code": "C2"}, "ai")
    assert sid == "s001" and svc.ai_stats()["resolved"] == 0
    assert svc.resolve_suggestion(sid, "C2") is True
    sid2 = svc.log_suggestion("cause", "def", {"code": "C1"}, "deterministic")
    assert svc.resolve_suggestion(sid2, "C3") is False
    st = svc.ai_stats()
    assert st == {"cause_suggestions": 2, "resolved": 2, "accepted": 1, "acceptance_rate": 0.5,
                  "by_source": {"ai": 1, "deterministic": 1}, "digest_rewrites": 0}
    with pytest.raises(ServiceError, match="找不到建议"):
        svc.resolve_suggestion("s999", "C1")
    with pytest.raises(ServiceError, match="kind"):
        svc.log_suggestion("other", "x", {}, "ai")


def test_cli_suggest_add_retag_stats(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REDO_RIGHT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    f = str(tmp_path / "d.json")
    main(["--file", f, "init", "--student", "乙", "--grade", "高一"])
    assert main(["--file", f, "--today", "2026-10-06", "suggest", "--problem", "p", "--note", "看漏了偶数条件"]) == 0
    out = capsys.readouterr().out
    assert "建议 C4 审题错误" in out and "建议编号 s001" in out
    assert main(["--file", f, "--today", "2026-10-06", "add", "--topic", "t", "--cause", "C4", "--problem", "p",
                 "--note", "看漏了偶数条件", "--suggestion", "s001"]) == 0
    assert "采纳了 AI 建议" in capsys.readouterr().out
    mid = Service(f).list()[0].id
    main(["--file", f, "--today", "2026-10-07", "redo", mid, "--fail"])
    main(["--file", f, "--today", "2026-10-08", "redo", mid, "--fail"])
    capsys.readouterr()
    assert main(["--file", f, "--today", "2026-10-08", "suggest", "--problem", "p", "--note", "其实是没想到要分类讨论", "--for", mid]) == 0
    assert "s002" in capsys.readouterr().out
    assert main(["--file", f, "--today", "2026-10-08", "retag", mid, "--cause", "C1", "--reason", "概念没掌握", "--suggestion", "s002"]) == 0
    assert "没有采纳 AI 建议" in capsys.readouterr().out
    assert Service(f).list()[0].cause_history[-1].by == "student"
    assert main(["--file", f, "ai-stats"]) == 0
    assert "已决定 2，采纳 1，采纳率 50%" in capsys.readouterr().out
    assert main(["--file", f, "--today", "2026-10-12", "parent", "--ai"]) == 0
    err = capsys.readouterr().err
    assert "未配置 API key" in err
    assert Service(f).ai_stats()["digest_rewrites"] == 1
