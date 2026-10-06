from datetime import date

import pytest

from redo_right import ladder as L

D0 = date(2026, 10, 6)


def test_default_ladder_is_1_7_21():
    assert L.DEFAULT_LADDER_DAYS == (1, 7, 21)


def test_initial_state_due_next_day():
    s = L.initial_state(D0)
    assert s.stage == 0 and s.fail_count == 0 and not s.closed
    assert s.due == date(2026, 10, 7)


def test_three_passes_close_the_loop():
    s = L.initial_state(D0)
    s = L.apply_result(s, True, date(2026, 10, 7))
    assert s.stage == 1 and s.due == date(2026, 10, 14)
    s = L.apply_result(s, True, date(2026, 10, 14))
    assert s.stage == 2 and s.due == date(2026, 11, 4)
    s = L.apply_result(s, True, date(2026, 11, 4))
    assert s.closed and s.due is None and s.stage == 3


def test_fail_resets_to_bottom_and_counts():
    s = L.initial_state(D0)
    s = L.apply_result(s, True, date(2026, 10, 7))
    s = L.apply_result(s, False, date(2026, 10, 14))
    assert s.stage == 0 and s.fail_count == 1 and s.due == date(2026, 10, 15)
    s = L.apply_result(s, False, date(2026, 10, 15))
    assert s.fail_count == 2  # 不随通过清零


def test_fail_count_survives_later_passes():
    s = L.initial_state(D0)
    s = L.apply_result(s, False, date(2026, 10, 7))
    s = L.apply_result(s, True, date(2026, 10, 8))
    assert s.fail_count == 1 and s.stage == 1


def test_cannot_redo_before_due():
    s = L.initial_state(D0)
    with pytest.raises(L.LadderError, match="还没到重做日"):
        L.apply_result(s, True, D0)


def test_late_redo_is_allowed():
    s = L.initial_state(D0)
    s = L.apply_result(s, True, date(2026, 10, 20))  # 迟了也算，下一档从实际重做日起算
    assert s.due == date(2026, 10, 27)


def test_cannot_redo_closed():
    s = L.LadderState(stage=3, fail_count=0, due=None, closed=True)
    with pytest.raises(L.LadderError, match="已经闭环"):
        L.apply_result(s, True, D0)


@pytest.mark.parametrize("bad", [[], [0], [-1, 2], [1, 1], [7, 1], [1, 2.5], [True]])
def test_invalid_ladders_rejected(bad):
    with pytest.raises(L.LadderError):
        L.validate_ladder(bad)


def test_custom_ladder_respected():
    s = L.initial_state(D0, [2, 5])
    assert s.due == date(2026, 10, 8)
    s = L.apply_result(s, True, date(2026, 10, 8), [2, 5])
    assert s.due == date(2026, 10, 13)
    s = L.apply_result(s, True, date(2026, 10, 13), [2, 5])
    assert s.closed


def test_describe_stage():
    assert L.describe_stage(0) == "第 1/3 次重做（间隔 1 天）"
    assert L.describe_stage(2) == "第 3/3 次重做（间隔 21 天）"
    assert L.describe_stage(3) == "已闭环"
