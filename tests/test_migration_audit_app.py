"""v0.5：schema 迁移、不变量审计、压测不变量 + 确定性、网页 API。"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from redo_right import store
from redo_right.app import create_app
from redo_right.audit import check_invariants
from redo_right.cli import main
from redo_right.model import SCHEMA_VERSION
from redo_right.service import Service
from redo_right.taxonomy import load_taxonomy
from tools.make_demo_class import build

TAX = load_taxonomy()

V1_DOC = {
    "schema_version": 1,
    "student": {"name": "旧", "grade": "高一"},
    "ladder": [1, 7, 21],
    "mistakes": [
        {"id": "x1", "created": "2026-10-01", "source": "作业", "topic": "t", "problem": "p", "cause": "C1",
         "note": "", "stage": 0, "fail_count": 0, "due": "2026-10-02", "status": "open",
         "attempts": [], "cause_history": [{"date": "2026-10-01", "from_code": None, "to_code": "C1",
                                             "reason": "首次归因", "by": "student"}]},
    ],
}


# ---------- 迁移 ----------

def test_schema_is_2():
    assert SCHEMA_VERSION == 2


def test_v1_file_loads_and_is_migrated(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps(V1_DOC, ensure_ascii=False), encoding="utf-8")
    doc = store.load(p)
    assert doc["schema_version"] == 2 and doc["ai_log"] == [] and doc["meta"]["migrated_from"] == 1
    m = doc["mistakes"][0]
    assert m["needs_retag"] is False and m["attention"] is False
    # 迁移后的文件能被 Service 正常使用并重新保存为 v2
    svc = Service(p, today_fn=lambda: date(2026, 10, 2))
    svc.redo("x1", True)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2 and raw["mistakes"][0]["stage"] == 1


def test_future_schema_rejected(tmp_path):
    p = tmp_path / "f.json"
    d = dict(V1_DOC, schema_version=99)
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(store.StoreError, match="升级程序"):
        store.load(p)


def test_v2_requires_ai_log(tmp_path):
    p = tmp_path / "b.json"
    d = dict(V1_DOC, schema_version=2)  # 声称 v2 却没有 ai_log
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(store.StoreError, match="ai_log"):
        store.load(p)


def test_demo_class_files_load(tmp_path):
    # 仓库内的演示班级在 v0.3 时以 v1 写出，必须能被当前版本直接打开
    from redo_right.classroom import load_class
    docs = load_class("examples/demo_class")
    assert len(docs) == 12 and all(d["schema_version"] == 2 for d in docs)


# ---------- 不变量审计 ----------

def test_audit_passes_on_clean_data_and_catches_corruption(tmp_path):
    svc = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 6), id_fn=iter(["a", "b"]).__next__)
    svc.init("甲", "高一")
    svc.add("t", "C1", "p")
    svc.add("t", "C2", "q")
    s2 = Service(tmp_path / "d.json", today_fn=lambda: date(2026, 10, 7))
    s2.redo("a", True)
    s2.redo("b", False)
    doc = svc.document()
    assert check_invariants(doc, TAX) == []
    # 手工破坏：fail_count 不符、闭环却有 due、待归因但失败不足两次、历史不一致
    bad = json.loads(json.dumps(doc))
    bad["mistakes"][0]["fail_count"] = 5
    bad["mistakes"][1]["needs_retag"] = True
    bad["mistakes"][1]["cause"] = "C3"
    problems = check_invariants(bad, TAX)
    assert any("fail_count=5" in p for p in problems)
    assert any("失败不足两次" in p for p in problems)
    assert any("归因历史末项" in p for p in problems)


def test_cli_audit(tmp_path, capsys):
    f = str(tmp_path / "d.json")
    main(["--file", f, "init", "--student", "甲", "--grade", "高一"])
    assert main(["--file", f, "audit"]) == 0
    assert "通过" in capsys.readouterr().out
    doc = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))
    doc["mistakes"].append({"id": "z", "created": "2026-10-01", "source": "作业", "topic": "t", "problem": "p",
                            "cause": "C9", "note": "", "stage": 0, "fail_count": 0, "due": None,
                            "status": "open", "needs_retag": False, "attention": False,
                            "attempts": [], "cause_history": []})
    (tmp_path / "d.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert main(["--file", f, "audit"]) == 1
    err = capsys.readouterr().err
    assert "不在错因表" in err and "没有下次重做日" in err


# ---------- 压测缩小版：不变量 + 确定性 ----------

def test_generated_cohort_satisfies_invariants_and_is_deterministic(tmp_path):
    build(tmp_path / "a", seed=3, weeks=5, n_students=10, backups=False)
    build(tmp_path / "b", seed=3, weeks=5, n_students=10, backups=False)
    files = sorted((tmp_path / "a").glob("*.json"))
    assert len(files) == 10
    total = 0
    for f in files:
        doc = store.load(f)
        total += len(doc["mistakes"])
        assert check_invariants(doc, TAX) == [], f.name
    assert total > 100
    for f in files:
        assert f.read_bytes() == (tmp_path / "b" / f.name).read_bytes()
    assert any(m["status"] == "closed" for f in files for m in store.load(f)["mistakes"])  # 5 周足够闭环


# ---------- 网页 API ----------

def client(tmp_path, today=date(2026, 10, 7)):
    return TestClient(create_app(tmp_path / "web.json", today_fn=lambda: today))


def test_api_uninitialized_then_init(tmp_path):
    c = client(tmp_path)
    st = c.get("/api/state").json()
    assert st["initialized"] is False and len(st["causes"]) == 6
    assert c.post("/api/init", json={"student": "乙", "grade": "高一"}).status_code == 200
    st = c.get("/api/state").json()
    assert st["initialized"] and st["student"]["name"] == "乙" and st["due"] == [] and st["week"]["added"] == 0
    assert c.post("/api/init", json={"student": "乙", "grade": "高一"}).status_code == 400


def test_api_full_loop_and_errors(tmp_path):
    c6 = client(tmp_path, date(2026, 10, 6))
    c6.post("/api/init", json={"student": "乙", "grade": "高一"})
    r = c6.post("/api/mistakes", json={"topic": "数列", "cause": "C2", "problem": "讲义 L3 例 4", "note": "没想到换元"})
    assert r.status_code == 200
    m = r.json()["mistake"]
    assert m["due"] == "2026-10-07" and m["cause_name"] == "思路未想到" and "信号词" in m["next_step"]
    assert c6.post("/api/mistakes", json={"topic": "", "cause": "C2", "problem": "x"}).status_code == 400
    assert c6.post(f"/api/mistakes/{m['id']}/redo", json={"passed": True}).status_code == 400  # 未到期
    c7 = client(tmp_path, date(2026, 10, 7))
    st = c7.get("/api/state").json()
    assert [d["id"] for d in st["due"]] == [m["id"]]
    assert c7.post(f"/api/mistakes/{m['id']}/redo", json={"passed": False, "seconds": 300}).status_code == 200
    c8 = client(tmp_path, date(2026, 10, 8))
    c8.post(f"/api/mistakes/{m['id']}/redo", json={"passed": False})
    st = c8.get("/api/state").json()
    assert [d["id"] for d in st["retag"]] == [m["id"]] and st["due"] == []
    assert c8.post(f"/api/mistakes/{m['id']}/redo", json={"passed": True}).status_code == 400
    sg = c8.post("/api/suggest", json={"problem": "讲义 L3 例 4", "note": "其实是看漏了条件", "for_id": m["id"]}).json()
    assert sg["code"] == "C4" and sg["id"] == "s001" and sg["cause_name"] == "审题错误"
    r = c8.post(f"/api/mistakes/{m['id']}/retag", json={"cause": "C4", "reason": "看漏了偶数条件", "suggestion": "s001"})
    assert r.status_code == 200 and r.json()["accepted"] is True
    assert r.json()["mistake"]["cause_history"][-1]["by"] == "ai-suggested-accepted"
    assert c8.get("/api/ai-stats").json()["acceptance_rate"] == 1.0
    weekly = c8.get("/api/report/weekly").text
    assert "乙 的本周错题周报" in weekly
    parent = c8.get("/api/report/parent").text
    assert "您这周能帮的一件事" in parent and "讲义 L3 例 4" not in parent
    assert c8.get("/").status_code == 200 and "再做对" in c8.get("/").text
