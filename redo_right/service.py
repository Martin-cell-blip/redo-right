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
            if m.status == STATUS_OPEN and m.due and date.fromisoformat(m.due) <= on
        ]
        out.sort(key=lambda m: (m.due or "", m.created))
        return out

    def redo(self, mistake_id: str, passed: bool, seconds: int | None = None, note: str = "") -> Mistake:
        doc = self._load()
        today = self._today()
        mistakes = self._mistakes(doc)
        m = self._find(mistakes, mistake_id)
        if m.status != STATUS_OPEN:
            raise ServiceError(f"错题 {m.id} 状态为 {m.status}，不能重做")
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

    def list(self, status: str | None = None) -> list[Mistake]:
        doc = self._load()
        ms = self._mistakes(doc)
        if status:
            ms = [m for m in ms if m.status == status]
        return sorted(ms, key=lambda m: (m.created, m.id))

    def document(self) -> dict:
        return self._load()
