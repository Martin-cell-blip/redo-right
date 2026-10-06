"""数据模型。所有日期都用 ISO 字符串 (YYYY-MM-DD) 存储，只用 date 不用 datetime——
产品只关心“哪一天重做”，不关心时分秒，也就不会有时区问题。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

SCHEMA_VERSION = 2  # v1: v0.1–v0.4；v2 (v0.5): ai_log 必填、二次错标记落盘、meta

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_ARCHIVED = "archived"
STATUSES = (STATUS_OPEN, STATUS_CLOSED, STATUS_ARCHIVED)

SOURCES = ("讲义", "作业", "模考", "其他")


@dataclass
class Attempt:
    date: str  # 重做日期
    passed: bool
    seconds: int | None = None  # 用时（限时模式）
    note: str = ""  # 学生一句话：这次是怎么想的


@dataclass
class CauseChange:
    date: str
    from_code: str | None
    to_code: str
    reason: str  # 为什么改归因（二次错后重标 / 老师纠正 / 学生自己想明白了）
    by: str  # student / teacher / ai-suggested-accepted


@dataclass
class Mistake:
    id: str
    created: str
    source: str  # 讲义 / 作业 / 模考 / 其他
    topic: str  # 知识点，例如 "数列-递推"
    problem: str  # 题目文字或引用（“讲义 L3 例 4”），不存答案
    cause: str  # 当前错因编码
    note: str  # 学生当时怎么想的（归因依据）
    stage: int = 0
    fail_count: int = 0
    due: str | None = None
    status: str = STATUS_OPEN
    needs_retag: bool = False  # 累计两次做错后，必须先重新归因才能再重做
    attention: bool = False  # 需要老师关注（二次错触发，老师处理后清除）
    attempts: list[Attempt] = field(default_factory=list)
    cause_history: list[CauseChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Mistake":
        attempts = [Attempt(**a) for a in d.get("attempts", [])]
        history = [CauseChange(**c) for c in d.get("cause_history", [])]
        fields = {k: v for k, v in d.items() if k not in ("attempts", "cause_history")}
        return cls(attempts=attempts, cause_history=history, **fields)

    def due_date(self) -> date | None:
        return date.fromisoformat(self.due) if self.due else None


def empty_document(student_name: str, grade: str, ladder: list[int]) -> dict:
    from . import __version__

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {"created_with": __version__},
        "student": {"name": student_name, "grade": grade},
        "ladder": list(ladder),
        "mistakes": [],
        "ai_log": [],
    }
