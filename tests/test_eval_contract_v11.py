"""v1.1：合同新规则（可留空 / 新数字 / 新方法名）、提示词版本、评测集与评测脚本、周报平票规则。全部离线。"""

import json
from collections import Counter
from datetime import date

import pytest

from redo_right import ai
from redo_right.report import top_failed_cause, weekly_summary
from redo_right.service import Service
from redo_right.taxonomy import load_taxonomy
from tools import eval_suggest as E

TAX = load_taxonomy()


# ---------- 合同 ----------

def raw(**kw):
    base = {"code": "C1", "evidence_quote": "以为 sin x 的周期是 π", "question": "你确定自己记的周期没问题吗？", "confidence": "high"}
    base.update(kw)
    return base


NOTE = "我一直以为 sin x 的周期是 π"


def test_leaking_correct_value_is_rejected():
    v = ai.validate_suggestion(raw(question="你确定周期是 π 而不是 2π 吗？"), NOTE, "讲义 L1 例 1", TAX)
    assert any("没有的数字" in x and "2" in x for x in v)


def test_method_hint_rejected_unless_student_said_it():
    note = "这题要用抽屉原理，我完全没往这个方向想"
    bad = raw(code="C2", evidence_quote="完全没往这个方向想", question="你是没想到要构造抽屉吗？")
    assert any("没有的方法" in x for x in ai.validate_suggestion(bad, note, "p", TAX))
    ok = raw(code="C2", evidence_quote="完全没往这个方向想", question="你是不知道抽屉原理，还是知道但没想起来？")
    assert ai.validate_suggestion(ok, note, "p", TAX) == []


def test_new_solution_signals():
    v = ai.validate_suggestion(raw(question="正确的是什么你知道吗？"), NOTE, "p", TAX)
    assert any("解题内容" in x for x in v)


def test_null_code_allowed_only_with_low_confidence():
    ok = {"code": None, "evidence_quote": "", "question": "你具体卡在哪一步？", "confidence": "low"}
    assert ai.validate_suggestion(ok, "粗心", "p", TAX) == []
    bad = dict(ok, confidence="high")
    assert any("必须是 low" in x for x in ai.validate_suggestion(bad, "粗心", "p", TAX))
    missing_quote = raw(evidence_quote="")
    assert any("缺依据引用" in x for x in ai.validate_suggestion(missing_quote, NOTE, "p", TAX))


def test_suggest_cause_can_return_no_code():
    fake = lambda m: json.dumps({"code": None, "evidence_quote": "", "question": "你具体卡在哪一步？", "confidence": "low"})
    sg = ai.suggest_cause("p", "粗心", TAX, call_fn=fake)
    assert sg.code is None and sg.source == "ai" and sg.prompt_version == ai.PROMPT_VERSION


def test_prompt_versions():
    assert ai.PROMPT_VERSION == "v2" and set(ai.CAUSE_PROMPT_GUIDES) >= {"v1", "v2"}
    v1 = ai._cause_prompt("p", "n", TAX, "v1")[0]["content"]
    v2 = ai._cause_prompt("p", "n", TAX, "v2")[0]["content"]
    assert "标注规则" not in v1 and "审题 C4 → 概念 C1 → 思路 C2 → 计算 C3 → 表达 C6" in v2 and "code 填 null" in v2
    with pytest.raises(ValueError):
        ai._cause_prompt("p", "n", TAX, "v9")


def test_null_suggestion_not_counted_in_acceptance(tmp_path):
    s = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 6))
    s.init("甲", "高一")
    sid = s.log_suggestion("cause", "x", {"code": None}, "ai")
    assert s.resolve_suggestion(sid, "C3") is None
    st = s.ai_stats()
    assert st["cause_suggestions"] == 1 and st["resolved"] == 0 and st["acceptance_rate"] is None


# ---------- 评测集与评测脚本 ----------

def test_dataset_integrity():
    items = E.load_items()
    assert len(items) == 84 and len({i["id"] for i in items}) == 84
    assert Counter(i["split"] for i in items) == {"dev": 56, "test": 28}
    for i in items:
        assert i["gold"] is None or TAX.has(i["gold"])
        assert (i["gold"] is None and i["acceptable"] == []) or i["gold"] in i["acceptable"]
        assert i["note"].strip() and i["category"] in {
            "clear", "paraphrase", "mixed", "adversarial", "leak_bait", "vague", "hard_mixed", "noisy"}
    for sp in ("dev", "test"):
        golds = {i["gold"] for i in items if i["split"] == sp and i["gold"]}
        assert golds == {"C1", "C2", "C3", "C4", "C5", "C6"}  # 每类错因两边都有


def test_baseline_numbers_are_pinned():
    """关键词基线是回退方案，它的成绩被钉住：改关键词表或评测集时这里会提醒你重新记录。"""
    s = E.summarize(E.run(E.load_items(), "baseline"))
    assert (s["all"]["strict_accuracy"], s["dev"]["strict_accuracy"], s["test"]["strict_accuracy"]) == (0.453, 0.48, 0.4)
    assert s["all"]["by_category"]["paraphrase"] == 0.0 and s["all"]["leaked"] == 0


def test_harness_with_oracle_model():
    items = {i["note"]: i for i in E.load_items()}

    def oracle(messages):
        note = messages[-1]["content"].split("我当时怎么想的”：", 1)[1]
        g = items[note]["gold"]
        if g is None:
            return json.dumps({"code": None, "evidence_quote": "", "question": "你具体卡在哪一步？", "confidence": "low"})
        return json.dumps({"code": g, "evidence_quote": note[:8], "question": "你确定是这一类吗？", "confidence": "high"},
                          ensure_ascii=False)

    s = E.summarize(E.run(E.load_items(), "ai", "v2", workers=1, call_fn=oracle))["all"]
    assert s["strict_accuracy"] == 1.0 and s["contract_first_try"] == 1.0 and s["fallback_rate"] == 0.0
    assert s["no_basis_null"] == 1.0 and s["leaked"] == 0 and s["overclaim"] == 0


def test_eval_leak_detector_is_independent_and_catches_values():
    assert E.leak_detail("你确定周期是 π 而不是 2π 吗？", NOTE, "讲义 L1 例 1") == ["数字 2"]
    assert E.leak_detail("你是没想到构造抽屉吗？", "要用抽屉原理", "p") == ["方法 构造"]
    assert E.leak_detail("那应该是多少？", "算错了", "p") == ["信号 应该是"]


# ---------- 周报平票 ----------

def test_top_failed_cause_tie_breaks_by_most_recent_failure():
    s = {"by_cause_failed": {"C2": 1, "C4": 1}, "last_fail_by_cause": {"C2": "2026-10-06", "C4": "2026-10-03"}}
    assert top_failed_cause(s) == ("C2", 1)  # 原来按编码会选 C4
    assert top_failed_cause({"by_cause_failed": {}}) is None
    assert top_failed_cause({"by_cause_failed": {"C3": 2, "C1": 1}}) == ("C3", 2)


def test_weekly_summary_records_last_fail(tmp_path):
    ids = iter(["a", "b"])
    s = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 1), id_fn=lambda: next(ids))
    s.init("甲", "高一")
    s.add("t", "C4", "p")
    s.add("t", "C2", "q")
    Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 2)).redo("a", False)
    Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 3)).redo("b", False)
    w = weekly_summary(s.document(), date(2026, 10, 6))
    assert w["last_fail_by_cause"] == {"C4": "2026-10-02", "C2": "2026-10-03"}
    assert top_failed_cause(w) == ("C2", 1)
