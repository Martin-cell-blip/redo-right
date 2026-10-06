"""数据一致性审计：把产品规则写成对任意数据文件都能跑的不变量。

用途：① `audit` 命令让学生/老师随时自检文件没坏；② 压测后机器验收；③ 迁移后回归。
每条违规带错题 id，直接可定位。

规则编号（README 与 CHANGELOG 引用同一编号）：
  R1  重做日期不早于记录日期
  R2  重做日期严格递增（一天只算一次）
  R3  fail_count 等于失败的重做次数
  R4  当前错因在错因表内
  R5  归因历史首项是首次归因、末项等于当前错因
  R6  在练：stage 等于末尾连续做对次数，且小于阶梯长度
  R7  在练：下次重做日 = 最近一次重做（无则记录日）+ 当前档间隔
  R8  闭环：末尾连续做对次数 ≥ 阶梯长度，stage = 阶梯长度，无下次重做日
  R9  待归因：至少两次失败，且最近一次重做是失败；已闭环不能待归因
  R10 老师关注：至少两次失败
  R11 归档：无下次重做日
"""

from __future__ import annotations

from datetime import date, timedelta

from .model import STATUS_ARCHIVED, STATUS_CLOSED, STATUS_OPEN, STATUSES, Mistake
from .taxonomy import Taxonomy

RETAG_MIN_FAILS = 2


def _trailing_passes(m: Mistake) -> int:
    n = 0
    for a in reversed(m.attempts):
        if not a.passed:
            break
        n += 1
    return n


def check_invariants(doc: dict, taxonomy: Taxonomy) -> list[str]:
    v: list[str] = []
    ladder = doc["ladder"]
    n = len(ladder)
    for raw in doc["mistakes"]:
        m = Mistake.from_dict(raw)
        tag = f"[{m.id}]"
        created = date.fromisoformat(m.created)
        dates = [date.fromisoformat(a.date) for a in m.attempts]
        if any(d < created for d in dates):
            v.append(f"{tag} R1 有重做日期早于记录日期")
        if any(b <= a for a, b in zip(dates, dates[1:])):
            v.append(f"{tag} R2 重做日期不是严格递增（同一天重复或乱序）")
        fails = sum(1 for a in m.attempts if not a.passed)
        if fails != m.fail_count:
            v.append(f"{tag} R3 fail_count={m.fail_count} 与失败次数 {fails} 不一致")
        if not taxonomy.has(m.cause):
            v.append(f"{tag} R4 错因 {m.cause} 不在错因表")
        if not m.cause_history or m.cause_history[-1].to_code != m.cause:
            v.append(f"{tag} R5 归因历史末项与当前错因不一致")
        elif m.cause_history[0].from_code is not None:
            v.append(f"{tag} R5 归因历史首项应为首次归因")
        trailing = _trailing_passes(m)
        last_failed = bool(m.attempts) and not m.attempts[-1].passed

        if m.status == STATUS_OPEN:
            if m.stage != trailing or m.stage >= n:
                v.append(f"{tag} R6 stage={m.stage} 与末尾连续做对 {trailing} 次不符（阶梯长 {n}）")
            elif m.due is None:
                v.append(f"{tag} R7 在练却没有下次重做日")
            else:
                base = dates[-1] if dates else created
                expect = base + timedelta(days=ladder[m.stage])
                if date.fromisoformat(m.due) != expect:
                    v.append(f"{tag} R7 下次重做日 {m.due} 应为 {expect.isoformat()}")
            if m.needs_retag and (m.fail_count < RETAG_MIN_FAILS or not last_failed):
                v.append(f"{tag} R9 待归因但失败不足两次或最近一次不是失败")
        elif m.status == STATUS_CLOSED:
            if trailing < n or m.stage != n or m.due is not None:
                v.append(f"{tag} R8 已闭环但连续做对 {trailing}/{n}、stage={m.stage}、due={m.due}")
            if m.needs_retag:
                v.append(f"{tag} R9 已闭环却仍标待归因")
        elif m.status == STATUS_ARCHIVED:
            if m.due is not None:
                v.append(f"{tag} R11 已归档却仍有下次重做日")
        if m.status not in STATUSES:
            v.append(f"{tag} 未知状态 {m.status}")
        if m.attention and m.fail_count < RETAG_MIN_FAILS:
            v.append(f"{tag} R10 老师关注但失败不足两次")
    return v
