"""v1.1：并发写入（线程 + 进程）、锁的可重入与失效回收、更严的审计规则、网页新接口。"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from redo_right import store
from redo_right.app import create_app
from redo_right.audit import check_invariants
from redo_right.service import Service
from redo_right.taxonomy import load_taxonomy

TAX = load_taxonomy()
ROOT = Path(__file__).resolve().parent.parent


# ---------- 并发 ----------

def test_threads_do_not_lose_writes(tmp_path):
    f = tmp_path / "c.json"
    Service(f).init("并发", "高一")
    errors = []

    def worker(k):
        s = Service(f)
        for i in range(5):
            try:
                s.add(f"t{k}", "C1", f"p{k}-{i}")
            except Exception as e:  # pragma: no cover - 失败时才走到
                errors.append(repr(e))

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    doc = Service(f).document()
    assert errors == []
    assert len(doc["mistakes"]) == 60
    assert check_invariants(doc, TAX) == []
    assert not [p for p in tmp_path.iterdir() if p.suffix in (".lock", ".tmp")]


def test_processes_do_not_lose_writes(tmp_path):
    f = tmp_path / "p.json"
    Service(f).init("多进程", "高一")
    code = (
        "import sys; sys.path.insert(0, {root!r});"
        "from redo_right.service import Service;"
        "s = Service({f!r});"
        "[s.add('t', 'C2', 'p%s-%d' % (sys.argv[1], i)) for i in range(4)]"
    ).format(root=str(ROOT), f=str(f))
    env = dict(os.environ, PYTHONUTF8="1")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(k)], env=env) for k in range(5)]
    assert [p.wait(timeout=60) for p in procs] == [0] * 5
    assert len(Service(f).list()) == 20


def test_same_day_double_submit_counts_once(tmp_path):
    """网页上连点两次“做对了”：两个线程同时提交，只能记一次。"""
    f = tmp_path / "d.json"
    s0 = Service(f, today_fn=lambda: date(2026, 10, 6), id_fn=lambda: "a1")
    s0.init("连点", "高一")
    s0.add("t", "C1", "p")
    results = []

    def click():
        try:
            Service(f, today_fn=lambda: date(2026, 10, 7)).redo("a1", True)
            results.append("ok")
        except Exception as e:
            results.append(str(e))

    ts = [threading.Thread(target=click) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    m = Service(f).list()[0]
    assert results.count("ok") == 1 and len(m.attempts) == 1 and m.stage == 1
    assert all("一天只算一次" in r for r in results if r != "ok")


def test_lock_is_reentrant_and_released(tmp_path):
    f = tmp_path / "r.json"
    with store.locked(f):
        with store.locked(f):  # 同一线程可重入
            assert (tmp_path / "r.json.lock").exists()
        assert (tmp_path / "r.json.lock").exists()
    assert not (tmp_path / "r.json.lock").exists()


def test_stale_lock_is_reclaimed(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    Service(f).init("锁", "高一")
    lp = tmp_path / "s.json.lock"
    lp.write_text("99999")
    old = time.time() - store.LOCK_STALE_S - 5
    os.utime(lp, (old, old))
    Service(f).add("t", "C1", "p")  # 不应该卡住
    assert len(Service(f).list()) == 1 and not lp.exists()


def test_live_foreign_lock_times_out(tmp_path, monkeypatch):
    f = tmp_path / "t.json"
    Service(f).init("锁", "高一")
    (tmp_path / "t.json.lock").write_text("12345")  # 新鲜的锁 = 另一个进程正在用
    monkeypatch.setattr(store, "LOCK_TIMEOUT_S", 0.2)
    with pytest.raises(store.LockTimeout, match="正被其他程序使用"):
        with store.locked(f, timeout=0.2):
            pass


# ---------- 审计新规则 ----------

def test_audit_due_date_rule_and_retag_rules(tmp_path):
    s = Service(tmp_path / "a.json", today_fn=lambda: date(2026, 10, 6), id_fn=iter(["x", "y"]).__next__)
    s.init("审计", "高一")
    s.add("t", "C1", "p")
    s.add("t", "C1", "q")
    doc = s.document()
    assert check_invariants(doc, TAX) == []
    bad = json.loads(json.dumps(doc))
    bad["mistakes"][0]["due"] = "2026-10-09"  # 应为 10-07
    bad["mistakes"][1]["attention"] = True  # 没失败过却被关注
    problems = check_invariants(bad, TAX)
    assert any("R7" in p and "应为 2026-10-07" in p for p in problems)
    assert any("R10" in p for p in problems)


def test_demo_class_passes_stricter_audit():
    from redo_right.classroom import load_class
    for doc in load_class(ROOT / "examples" / "demo_class"):
        assert check_invariants(doc, TAX) == [], doc["student"]["name"]


# ---------- 网页新接口 ----------

def client(tmp_path, today):
    return TestClient(create_app(tmp_path / "w.json", today_fn=lambda: today, taxonomy=TAX))


def test_head_index_and_state_has_open_list(tmp_path):
    c = client(tmp_path, date(2026, 10, 6))
    assert c.head("/").status_code == 200
    assert c.get("/api/state").headers["cache-control"] == "no-store"
    assert c.post("/api/init", json={"student": " ", "grade": "高一"}).status_code == 400
    c.post("/api/init", json={"student": "丙", "grade": "高一"})
    a = c.post("/api/mistakes", json={"topic": "数列", "cause": "C2", "problem": "讲义 L3 例 4"}).json()["mistake"]
    b = c.post("/api/mistakes", json={"topic": "函数", "cause": "C3", "problem": "作业 2 第 5 题"}).json()["mistake"]
    assert a["ladder_len"] == 3 and a["redo_mode"] == "explain_first" and b["redo_mode"] == "timed"
    st = c.get("/api/state").json()
    assert [m["id"] for m in st["open"]] == [a["id"], b["id"]] and st["due"] == []


def test_archive_endpoint_and_sheet(tmp_path):
    c6 = client(tmp_path, date(2026, 10, 6))
    c6.post("/api/init", json={"student": "丙", "grade": "高一"})
    m = c6.post("/api/mistakes", json={"topic": "数列", "cause": "C2", "problem": "<b>讲义</b> L3"}).json()["mistake"]
    c7 = client(tmp_path, date(2026, 10, 7))
    sheet = c7.get("/api/sheet")
    assert sheet.status_code == 200 and "错题重练单" in sheet.text and "&lt;b&gt;讲义" in sheet.text
    assert c7.get("/api/sheet?on=bad-date").status_code == 400
    assert c7.post(f"/api/mistakes/{m['id']}/archive", json={"reason": " "}).status_code == 400
    r = c7.post(f"/api/mistakes/{m['id']}/archive", json={"reason": "超纲"})
    assert r.status_code == 200 and r.json()["mistake"]["status"] == "archived"
    st = c7.get("/api/state").json()
    assert st["open"] == [] and st["due"] == []
    assert c7.get("/api/report/weekly?week_end=2026-13-01").status_code == 400
