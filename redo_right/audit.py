"""数据一致性审计：把产品规则写成对任意数据文件都能跑的不变量。

用途：① `audit` 命令让学生/老师随时自检文件没坏；② 压测后机器验收；③ 迁移后回归。
每条违规带错题 id，直接可定位。
"""

from __future__ import annotations

from datetime import date

from .model import STATUS_ARCHIVED, STATUS_CLOSED, STATUS_OPEN, Mistake
from .taxonomy import Taxonomy


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
            v.append(f"{tag} 有重做日期早于记录日期")
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            v.append(f"{tag} 重做日期不是严格递增（同一天重复或乱序）")
        fails = sum(1 for a in m.attempts if not a.passed)
        if fails != m.fail_count:
            v.append(f"{tag} fail_count={m.fail_count} 与失败次数 {fails} 不一致")
        trailing = 0
        for a in reversed(m.attempts):
            if a.passed:
                trailing += 1
            else:
                break
        if not taxonomy.has(m.cause):
            v.append(f"{tag} 错因 {m.cause} 不在错因表")
        if not m.cause_history or m.cause_history[-1].to_code != m.cause:
            v.append(f"{tag} 归因历史末项与当前错因不一致")
        elif m.cause_history[0].from_code is not None:
            v.append(f"{tag} 归因历史首项应为首次归因")
        if m.status == STATUS_CLOSED:
            if m.due is not None or m.stage != n:
                v.append(f"{tag} 已闭环但 due/stage 不对")
            if trailing < n:
                v.append(f"{tag} 已闭环但末尾连续通过不足 {n} 次")
            if m.needs_retag:
                v.append(f"{tag} 已闭环却仍标待归因")
        elif m.status == STATUS_OPEN:
            if m.due is None:
                v.append(f"{tag} 在练却没有下次重做日")
            elif date.fromisoformat(m.due) <= created:
                v.append(f"{tag} 下次重做日不晚于记录日")
            if m.stage != min(trailing, n - 1) and not (m.stage == trailing):
                v.append(f"{tag} stage={m.stage} 与末尾连续通过 {trailing} 不一致")
            if m.needs_retag and m.fail_count < 2:
                v.append(f"{tag} 待归因但失败不足两次")
        elif m.status == STATUS_ARCHIVED:
            if m.due is not None:
                v.append(f"{tag} 已归档却仍有重做日")
        else:
            v.append(f"{tag} 未知状态 {m.status}")
    return v
