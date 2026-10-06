import json

import pytest

from redo_right import store
from redo_right.model import SCHEMA_VERSION, empty_document
from redo_right.taxonomy import TaxonomyError, load_taxonomy, taxonomy_from_dict


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "d.json"
    doc = empty_document("小明", "高一", [1, 7, 21])
    store.save(p, doc, backup=False)
    assert store.load(p) == doc


def test_missing_file_has_actionable_error(tmp_path):
    with pytest.raises(store.StoreError, match="先运行 init"):
        store.load(tmp_path / "nope.json")


def test_corrupt_json_rejected(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(store.StoreError, match="合法 JSON"):
        store.load(p)


def test_wrong_schema_version_rejected(tmp_path):
    p = tmp_path / "d.json"
    doc = empty_document("a", "b", [1])
    doc["schema_version"] = SCHEMA_VERSION + 99
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(store.StoreError, match="schema_version"):
        store.load(p)


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "d.json"
    doc = empty_document("a", "b", [1])
    m = {"id": "x", "created": "2026-10-06", "topic": "t", "cause": "C1", "status": "open"}
    doc["mistakes"] = [m, dict(m)]
    with pytest.raises(store.StoreError, match="重复"):
        store.save(p, doc)


def test_atomic_write_leaves_no_tmp_and_backs_up(tmp_path):
    p = tmp_path / "d.json"
    doc = empty_document("a", "b", [1])
    store.save(p, doc, backup=False)
    for i in range(7):
        doc["student"]["grade"] = f"g{i}"
        store.save(p, doc)
    assert not (tmp_path / "d.json.tmp").exists()
    backups = list((tmp_path / "d.json.bak").glob("d.*.json"))
    assert len(backups) == store.BACKUP_KEEP
    assert store.load(p)["student"]["grade"] == "g6"


def test_default_taxonomy_loads_six_causes():
    t = load_taxonomy()
    assert t.codes() == ("C1", "C2", "C3", "C4", "C5", "C6")
    assert t.get("C3").redo_mode == "timed"
    assert t.has("C1") and not t.has("C9")


def test_taxonomy_rejects_bad_configs():
    base = {"causes": [{"code": "A", "name": "n", "description": "d", "next_step": "s", "redo_mode": "plain"}]}
    with pytest.raises(TaxonomyError, match="至少要有两项"):
        taxonomy_from_dict(base)
    two = {"causes": base["causes"] + [dict(base["causes"][0])]}
    with pytest.raises(TaxonomyError, match="重复"):
        taxonomy_from_dict(two)
    bad_mode = {"causes": [dict(base["causes"][0], code="B", redo_mode="magic"), base["causes"][0]]}
    with pytest.raises(TaxonomyError, match="redo_mode"):
        taxonomy_from_dict(bad_mode)
    missing = {"causes": [{"code": "B", "name": "n"}, base["causes"][0]]}
    with pytest.raises(TaxonomyError, match="缺字段"):
        taxonomy_from_dict(missing)
