"""错因表：教研可配置的 JSON，产品只认编码不认措辞。

来源：UCD 微观经济学助教两学年批改作业时使用的评分反馈 checklist——
同一批作业里反复出现的不是“不会这道题”，而是少数几种可归类的错因。
把错因编码化之后，三件事才可能发生：
1. 学生重做时知道该练什么（概念 / 思路 / 计算 / 审题 / 时间 / 表达）；
2. 家长周报可以说“最常见的错因是 X”，而不是“错了 7 道题”；
3. 教研能看到 知识点 × 错因 的热力图，决定讲义该补例题还是改题干。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "default_causes.json"


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True)
class Cause:
    code: str  # 如 C1
    name: str  # 概念不清
    description: str  # 判定提示
    next_step: str  # 重做前要先做的一件事
    redo_mode: str  # 重做方式：plain / timed / explain_first / full_steps


@dataclass(frozen=True)
class Taxonomy:
    version: str
    causes: tuple[Cause, ...]

    def codes(self) -> tuple[str, ...]:
        return tuple(c.code for c in self.causes)

    def get(self, code: str) -> Cause:
        for c in self.causes:
            if c.code == code:
                return c
        raise TaxonomyError(f"未知错因编码 {code!r}，可用：{', '.join(self.codes())}")

    def has(self, code: str) -> bool:
        return any(c.code == code for c in self.causes)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "causes": [c.__dict__ for c in self.causes],
        }


_REQUIRED = ("code", "name", "description", "next_step", "redo_mode")
_REDO_MODES = ("plain", "timed", "explain_first", "full_steps")


def taxonomy_from_dict(data: dict) -> Taxonomy:
    if not isinstance(data, dict) or "causes" not in data:
        raise TaxonomyError("错因表必须是含 causes 列表的对象")
    causes: list[Cause] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["causes"]):
        missing = [k for k in _REQUIRED if k not in raw or not str(raw[k]).strip()]
        if missing:
            raise TaxonomyError(f"错因第 {i + 1} 项缺字段：{missing}")
        if raw["redo_mode"] not in _REDO_MODES:
            raise TaxonomyError(f"错因 {raw['code']} 的 redo_mode 非法：{raw['redo_mode']!r}")
        if raw["code"] in seen:
            raise TaxonomyError(f"错因编码重复：{raw['code']}")
        seen.add(raw["code"])
        causes.append(Cause(**{k: str(raw[k]).strip() for k in _REQUIRED}))
    if len(causes) < 2:
        raise TaxonomyError("错因表至少要有两项，否则归因没有意义")
    return Taxonomy(version=str(data.get("version", "custom")), causes=tuple(causes))


def load_taxonomy(path: Path | str | None = None) -> Taxonomy:
    p = Path(path) if path else DEFAULT_TAXONOMY_PATH
    with open(p, "r", encoding="utf-8") as f:
        return taxonomy_from_dict(json.load(f))
