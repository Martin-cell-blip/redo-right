"""单文件 JSON 存储。稳定性四件事：原子写、自动备份、模式校验、版本迁移。

- 原子写：先写临时文件再 os.replace，断电/崩溃也不会留下半个 JSON。
- 备份：每次成功写入前把旧文件存进 <file>.bak/，保留最近 N 份。
- 校验：加载时检查 schema_version 与必需字段，坏文件立刻报错而不是静默吞掉。
- 迁移：旧版本文件在加载时原地升级到当前 schema；未来版本的文件拒绝打开。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .model import SCHEMA_VERSION, STATUSES

BACKUP_KEEP = 5


class StoreError(ValueError):
    pass


class LockTimeout(StoreError):
    pass


# ---------- 文件锁 ----------
# 同一数据文件同一时刻只允许一个读写者：线程间用可重入锁，进程间用 <file>.lock 独占创建。
# v1.1 之前没有锁：网页服务的线程池里两个请求同时写，后写的覆盖先写的（实测 16 线程 × 5 次写入只剩 1 条）。

LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 30.0  # 持锁进程崩溃留下的锁文件，超过这个时间视为失效
_thread_locks: dict[str, threading.RLock] = {}
_registry_guard = threading.Lock()
_depth = threading.local()


def _lock_key(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))


def _acquire_lockfile(lp: Path, deadline: float) -> None:
    while True:
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except (FileExistsError, PermissionError):
            try:
                if time.time() - lp.stat().st_mtime > LOCK_STALE_S:
                    lp.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise LockTimeout(f"数据文件正被其他程序使用：{lp.with_suffix('')}（稍后再试；若确定没有程序在用，删除 {lp.name}）")
            time.sleep(0.005)


def _release_lockfile(lp: Path) -> None:
    for _ in range(50):
        try:
            lp.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.005)


@contextmanager
def locked(path: Path | str, timeout: float = LOCK_TIMEOUT_S):
    p = Path(path)
    key = _lock_key(p)
    with _registry_guard:
        rl = _thread_locks.setdefault(key, threading.RLock())
    if not rl.acquire(timeout=timeout):
        raise LockTimeout(f"数据文件忙：{p}")
    depths = getattr(_depth, "d", None)
    if depths is None:
        depths = _depth.d = {}
    outer = depths.get(key, 0) == 0
    lp = p.with_name(p.name + ".lock")
    try:
        if outer:
            p.parent.mkdir(parents=True, exist_ok=True)
            _acquire_lockfile(lp, time.monotonic() + timeout)
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
            if outer:
                _release_lockfile(lp)
    finally:
        rl.release()


def migrate(doc: dict) -> dict:
    """把任意旧版本文档升级到 SCHEMA_VERSION。每一步只做一件事，可测。"""
    if not isinstance(doc, dict):
        raise StoreError("数据文件顶层必须是对象")
    v = doc.get("schema_version")
    if v == 1:
        # v1 (v0.1–v0.4)：没有 ai_log，二次错标记是 v0.2 才有的可选字段
        doc.setdefault("ai_log", [])
        for m in doc.get("mistakes", []):
            m.setdefault("needs_retag", False)
            m.setdefault("attention", False)
            m.setdefault("attempts", [])
            m.setdefault("cause_history", [])
        doc.setdefault("meta", {})["migrated_from"] = 1
        doc["schema_version"] = 2
        v = 2
    if v != SCHEMA_VERSION:
        raise StoreError(f"数据文件 schema_version={v!r}，本版本只认 ≤{SCHEMA_VERSION}（更新的文件请升级程序）")
    return doc


def _validate(doc: dict) -> None:
    if not isinstance(doc, dict):
        raise StoreError("数据文件顶层必须是对象")
    v = doc.get("schema_version")
    if v != SCHEMA_VERSION:
        raise StoreError(f"数据文件 schema_version={v!r}，本版本只认 {SCHEMA_VERSION}")
    for key in ("student", "ladder", "mistakes", "ai_log"):
        if key not in doc:
            raise StoreError(f"数据文件缺少字段 {key!r}")
    if not isinstance(doc["mistakes"], list) or not isinstance(doc["ai_log"], list):
        raise StoreError("mistakes / ai_log 必须是列表")
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
    doc = migrate(doc)
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


def save(path: Path | str, doc: dict, backup: bool = True, fsync: bool = True) -> None:
    """fsync=False 只给批量生成/压测用：少一次落盘等待，但断电可能丢最后一笔。产品路径永远 True。"""
    p = Path(path)
    _validate(doc)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        _backup(p)
    # 临时文件名带进程与线程号：两个写者不会写进同一个临时文件
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False, indent=2))  # 一次性序列化 + 单次写入；json.dump 逐块流式写入走纯 Python 编码器（实测生成 5 名学生 9.4s → 4.4s）
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        _replace_with_retry(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _replace_with_retry(src: Path, dst: Path, attempts: int = 40) -> None:
    """Windows 下目标文件被其他句柄（杀毒软件、索引、只读的班级视图）短暂打开时 os.replace 会拒绝访问。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise StoreError(f"写入失败：{dst} 被其他程序占用")
            time.sleep(0.01)


def exists(path: Path | str) -> bool:
    return Path(path).exists()
