"""业务服务：把阶梯、错因表、存储串起来。时间与 id 都可注入，测试可完全确定。"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Callable

from . import ladder as L
from . import store
from .model import (
    STATUS_ARCHIVED,
    STATUS_CLOSED,
    STATUS_OPEN,
    SOURCES,
    Attempt,
    CauseChange,
    Mistake,
    empty_document,
)
from .taxonomy import Taxonomy, load_taxonomy


class ServiceError(ValueError):
    pass


# 二次错阈值。Anki 的 leech 规则是 8 次失败才挂起一张卡；竞赛题一次重做要 10–20 分钟，
# 同一道题累计错两次已经是强信号：要么错因标错了，要么需要老师介入。
RETAG_THRESHOLD = 2


def _default_id() -> str:
    return uuid.uuid4().hex[:8]


class Service:
    def __init__(
        self,
        path: Path | str,
        taxonomy: Taxonomy | None = None,
        today_fn: Callable[[], date] = date.today,
        id_fn: Callable[[], str] = _default_id,
    ):
        self.path = Path(path)
        self.taxonomy = taxonomy or load_taxonomy()
        self._today = today_fn
        self._new_id = id_fn

    # ---------- 生命周期 ----------
    def init(self, student_name: str, grade: str, ladder: list[int] | None = None) -> dict:
        if store.exists(self.path):
            raise ServiceError(f"数据文件已存在：{self.path}")
        ladder_t = L.validate_ladder(ladder or list(L.DEFAULT_LADDER_DAYS))
        doc = empty_document(student_name.strip(), grade.strip(), list(ladder_t))
        store.save(self.path, doc, backup=False)
        return doc

    def _load(self) -> dict:
        return store.load(self.path)

    def _save(self, doc: dict) -> None:
        store.save(self.path, doc)

    def _mistakes(self, doc: dict) -> list[Mistake]:
        return [Mistake.from_dict(m) for m in doc["mistakes"]]

    def _write_back(self, doc: dict, mistakes: list[Mistake]) -> None:
        doc["mistakes"] = [m.to_dict() for m in mistakes]
        self._save(doc)

    def _find(self, mistakes: list[Mistake], mistake_id: str) -> Mistake:
        for m in mistakes:
            if m.id == mistake_id:
                return m
        raise ServiceError(f"找不到错题 {mistake_id!r}")

    # ---------- 核心操作 ----------
    def add(self, topic: str, cause: str, problem: str, source: str = "作业", note: str = "") -> Mistake:
        if not topic.strip():
            raise ServiceError("知识点不能为空")
        if not problem.strip():
            raise ServiceError("题目引用不能为空（写题号或题目开头即可，不要写答案）")
        if source not in SOURCES:
            raise ServiceError(f"来源必须是 {SOURCES} 之一")
        self.taxonomy.get(cause)  # 未知编码直接报错
        doc = self._load()
        today = self._today()
        state = L.initial_state(today, doc["ladder"])
        m = Mistake(
            id=self._new_id(),
            created=today.isoformat(),
            source=source,
            topic=topic.strip(),
            problem=problem.strip(),
            cause=cause,
            note=note.strip(),
            stage=state.stage,
            fail_count=state.fail_count,
            due=state.due.isoformat() if state.due else None,
            status=STATUS_OPEN,
            cause_history=[CauseChange(today.isoformat(), None, cause, "首次归因", "student")],
        )
        mistakes = self._mistakes(doc)
        mistakes.append(m)
        self._write_back(doc, mistakes)
        return m

    def due(self, on: date | None = None) -> list[Mistake]:
        on = on or self._today()
        doc = self._load()
        out = [
            m
            for m in self._mistakes(doc)
            if m.status == STATUS_OPEN
            and not m.needs_retag  # 二次错的题先归因，不进重做清单
            and m.due
            and date.fromisoformat(m.due) <= on
        ]
        out.sort(key=lambda m: (m.due or "", m.created))
        return out

    def pending_retag(self) -> list[Mistake]:
        doc = self._load()
        return sorted(
            (m for m in self._mistakes(doc) if m.status == STATUS_OPEN and m.needs_retag),
            key=lambda m: (m.created, m.id),
        )

    def redo(self, mistake_id: str, passed: bool, seconds: int | None = None, note: str = "") -> Mistake:
        doc = self._load()
        today = self._today()
        mistakes = self._mistakes(doc)
        m = self._find(mistakes, mistake_id)
        if m.status != STATUS_OPEN:
            raise ServiceError(f"错题 {m.id} 状态为 {m.status}，不能重做")
        if m.needs_retag:
            raise ServiceError(
                f"错题 {m.id} 已累计 {m.fail_count} 次做错，先用 retag 重新归因（想清楚到底错在哪一类）再重做"
            )
        if any(a.date == today.isoformat() for a in m.attempts):
            raise ServiceError("今天已经记录过这道题的重做，一天只算一次")
        if seconds is not None and seconds < 0:
            raise ServiceError("用时不能为负")
        state = L.LadderState(m.stage, m.fail_count, m.due_date(), False)
        try:
            new = L.apply_result(state, passed, today, doc["ladder"])
        except L.LadderError as e:
            raise ServiceError(str(e)) from e
        m.attempts.append(Attempt(today.isoformat(), passed, seconds, note.strip()))
        m.stage, m.fail_count = new.stage, new.fail_count
        m.due = new.due.isoformat() if new.due else None
        if new.closed:
            m.status = STATUS_CLOSED
        if not passed and m.fail_count >= RETAG_THRESHOLD:
            m.needs_retag = True
            m.attention = True
        self._write_back(doc, mistakes)
        return m

    def retag(self, mistake_id: str, new_cause: str, reason: str, by: str = "student") -> Mistake:
        """重新归因。允许标回同一编码，但必须写理由——理由本身就是反思。"""
        self.taxonomy.get(new_cause)
        if not reason.strip():
            raise ServiceError("重新归因必须写理由（这次想清楚了错在哪？）")
        if by not in ("student", "teacher", "ai-suggested-accepted"):
            raise ServiceError("by 必须是 student / teacher / ai-suggested-accepted")
        doc = self._load()
        mistakes = self._mistakes(doc)
        m = self._find(mistakes, mistake_id)
        if m.status != STATUS_OPEN:
            raise ServiceError(f"错题 {m.id} 状态为 {m.status}，不需要重新归因")
        m.cause_history.append(
            CauseChange(self._today().isoformat(), m.cause, new_cause, reason.strip(), by)
        )
        m.cause = new_cause
        m.needs_retag = False
        self._write_back(doc, mistakes)
        return m

    def clear_attention(self, mistake_id: str, note: str) -> Mistake:
        """老师处理完（讲过了 / 换题了）后清除关注标记，须留一句处理记录。"""
        if not note.strip():
            raise ServiceError("清除关注必须写处理记录")
        doc = self._load()
        mistakes = self._mistakes(doc)
        m = self._find(mistakes, mistake_id)
        m.attention = False
        m.note = (m.note + f"\n[老师 {self._today().isoformat()}] {note.strip()}").strip()
        self._write_back(doc, mistakes)
        return m

    def archive(self, mistake_id: str, reason: str) -> Mistake:
        if not reason.strip():
            raise ServiceError("归档必须写原因（例如：超纲 / 题目有误 / 老师决定不练）")
        doc = self._load()
        mistakes = self._mistakes(doc)
        m = self._find(mistakes, mistake_id)
        if m.status == STATUS_CLOSED:
            raise ServiceError("已闭环的题不需要归档")
        m.status = STATUS_ARCHIVED
        m.due = None
        m.note = (m.note + f"\n[归档 {self._today().isoformat()}] {reason.strip()}").strip()
        self._write_back(doc, mistakes)
        return m

    # ---------- AI 建议日志（为“采纳率”留数据） ----------
    def log_suggestion(self, kind: str, input_sha: str, output: dict, source: str,
                       mistake_id: str | None = None) -> str:
        if kind not in ("cause", "digest"):
            raise ServiceError("kind 必须是 cause / digest")
        doc = self._load()
        log = doc.setdefault("ai_log", [])
        sid = f"s{len(log) + 1:03d}"
        log.append(
            {"id": sid, "date": self._today().isoformat(), "kind": kind, "input_sha": input_sha,
             "output": output, "source": source, "mistake_id": mistake_id, "accepted": None}
        )
        self._save(doc)
        return sid

    def resolve_suggestion(self, sid: str, chosen_code: str) -> bool:
        """学生最终选了哪个编码；与建议一致记 accepted=True。返回是否一致。"""
        doc = self._load()
        for e in doc.get("ai_log", []):
            if e["id"] == sid:
                if e["kind"] != "cause":
                    raise ServiceError(f"{sid} 不是错因建议")
                accepted = e["output"].get("code") == chosen_code
                e["accepted"] = accepted
                e["chosen_code"] = chosen_code
                self._save(doc)
                return accepted
        raise ServiceError(f"找不到建议 {sid!r}")

    def ai_stats(self) -> dict:
        log = self._load().get("ai_log", [])
        cause = [e for e in log if e["kind"] == "cause"]
        resolved = [e for e in cause if e["accepted"] is not None]
        accepted = [e for e in resolved if e["accepted"]]
        by_source = {}
        for e in cause:
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        return {
            "cause_suggestions": len(cause),
            "resolved": len(resolved),
            "accepted": len(accepted),
            "acceptance_rate": (len(accepted) / len(resolved)) if resolved else None,
            "by_source": by_source,
            "digest_rewrites": sum(1 for e in log if e["kind"] == "digest"),
        }

    def list(self, status: str | None = None) -> list[Mistake]:
        doc = self._load()
        ms = self._mistakes(doc)
        if status:
            ms = [m for m in ms if m.status == status]
        return sorted(ms, key=lambda m: (m.created, m.id))

    def document(self) -> dict:
        return self._load()
