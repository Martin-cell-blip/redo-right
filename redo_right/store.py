"""单文件 JSON 存储。稳定性三件事：原子写、自动备份、模式校验。

- 原子写：先写临时文件再 os.replace，断电/崩溃也不会留下半个 JSON。
- 备份：每次成功写入前把旧文件存进 <file>.bak/，保留最近 N 份。
- 校验：加载时检查 schema_version 与必需字段，坏文件立刻报错而不是静默吞掉。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .model import SCHEMA_VERSION, STATUSES

BACKUP_KEEP = 5


class StoreError(ValueError):
    pass


def _validate(doc: dict) -> None:
    if not isinstance(doc, dict):
        raise StoreError("数据文件顶层必须是对象")
    v = doc.get("schema_version")
    if v != SCHEMA_VERSION:
        raise StoreError(f"数据文件 schema_version={v!r}，本版本只认 {SCHEMA_VERSION}")
    for key in ("student", "ladder", "mistakes"):
        if key not in doc:
            raise StoreError(f"数据文件缺少字段 {key!r}")
    if not isinstance(doc["mistakes"], list):
        raise StoreError("mistakes 必须是列表")
    ids: set[str] = set()
    for m in doc["mistakes"]:
        for key in ("id", "created", "topic", "cause", "status"):
            if key not in m:
                raise StoreError(f"错题记录缺少字段 {key!r}: {m}")
        if m["status"] not in STATUSES:
            raise StoreError(f"错题 {m['id']} 状态非法：{m['status']!r}")
        if m["id"] in ids:
            raise StoreError(f"错题 id 重复：{m['id']}")
        ids.add(m["id"])


def load(path: Path | str) -> dict:
    p = Path(path)
    if not p.exists():
        raise StoreError(f"数据文件不存在：{p}（先运行 init）")
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        raise StoreError(f"数据文件不是合法 JSON：{e}") from e
    _validate(doc)
    return doc


def _backup(p: Path, keep: int = BACKUP_KEEP) -> None:
    if not p.exists():
        return
    bdir = p.parent / (p.name + ".bak")
    bdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    shutil.copy2(p, bdir / f"{p.stem}.{stamp}{p.suffix}")
    olds = sorted(bdir.glob(f"{p.stem}.*{p.suffix}"))
    for old in olds[:-keep] if len(olds) > keep else []:
        old.unlink()


def save(path: Path | str, doc: dict, backup: bool = True) -> None:
    p = Path(path)
    _validate(doc)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        _backup(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def exists(path: Path | str) -> bool:
    return Path(path).exists()
