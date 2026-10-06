"""间隔阶梯：错题重做的状态机。纯函数，无 I/O。

产品决策（v0.1）：
- 固定阶梯而非自适应算法（FSRS/SM-2）。原因：家长和学生要能一句话解释
  “为什么今天要重做这道题”；每个学生的错题样本太小，拟合不出稳定参数；
  固定阶梯零依赖、零漂移，便于测试。
- 默认 1 / 7 / 21 天：隔天查“当时是不是真懂了”，一周后查“有没有忘”，
  三周后查“能不能稳定做对”。三次连续通过才算闭环。
- 做错一次就回到阶梯底部，并累计 fail_count；fail_count 由上层决定是否触发
  “二次错”规则（v0.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

DEFAULT_LADDER_DAYS: tuple[int, ...] = (1, 7, 21)


class LadderError(ValueError):
    """阶梯规则被违反（配置非法、重做时机不对、已闭环再重做）。"""


def validate_ladder(ladder: Sequence[int]) -> tuple[int, ...]:
    if not ladder:
        raise LadderError("阶梯至少要有一个间隔")
    out: list[int] = []
    for i, d in enumerate(ladder):
        if not isinstance(d, int) or isinstance(d, bool) or d <= 0:
            raise LadderError(f"阶梯第 {i + 1} 项必须是正整数天数，得到 {d!r}")
        if out and d <= out[-1]:
            raise LadderError("阶梯间隔必须严格递增")
        out.append(d)
    return tuple(out)


@dataclass(frozen=True)
class LadderState:
    stage: int  # 已连续通过的重做次数，0..len(ladder)
    fail_count: int  # 累计做错次数（不随通过清零）
    due: date | None  # 下次重做日；闭环后为 None
    closed: bool


def initial_state(today: date, ladder: Sequence[int] = DEFAULT_LADDER_DAYS) -> LadderState:
    ladder = validate_ladder(ladder)
    return LadderState(stage=0, fail_count=0, due=today + timedelta(days=ladder[0]), closed=False)


def can_redo(state: LadderState, today: date) -> tuple[bool, str]:
    """重做时机检查。提前重做不计入阶梯：间隔的意义就是不要当天再做一遍。"""
    if state.closed:
        return False, "这道题已经闭环，不需要再重做"
    assert state.due is not None
    if today < state.due:
        return False, f"还没到重做日，下次重做：{state.due.isoformat()}"
    return True, ""


def apply_result(
    state: LadderState,
    passed: bool,
    today: date,
    ladder: Sequence[int] = DEFAULT_LADDER_DAYS,
) -> LadderState:
    ladder = validate_ladder(ladder)
    ok, why = can_redo(state, today)
    if not ok:
        raise LadderError(why)
    if passed:
        stage = state.stage + 1
        if stage >= len(ladder):
            return LadderState(stage=stage, fail_count=state.fail_count, due=None, closed=True)
        return LadderState(
            stage=stage,
            fail_count=state.fail_count,
            due=today + timedelta(days=ladder[stage]),
            closed=False,
        )
    return LadderState(
        stage=0,
        fail_count=state.fail_count + 1,
        due=today + timedelta(days=ladder[0]),
        closed=False,
    )


def describe_stage(stage: int, ladder: Sequence[int] = DEFAULT_LADDER_DAYS) -> str:
    ladder = validate_ladder(ladder)
    if stage >= len(ladder):
        return "已闭环"
    return f"第 {stage + 1}/{len(ladder)} 次重做（间隔 {ladder[stage]} 天）"
