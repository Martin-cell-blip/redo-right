"""压测：N 个学生 × W 周，全部走产品 Service，然后跑十条不变量审计并计时。

用法：python tools/stress.py --students 300 --weeks 6 [--seed 7] [--keep]
输出：生成耗时、每学生平均耗时、文件大小、审计结果、同 seed 双跑是否逐字节一致。
⚠️ 合成数据，只用于稳定性验收，不代表任何教学效果。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redo_right.audit import check_invariants  # noqa: E402
from redo_right.store import load  # noqa: E402
from redo_right.taxonomy import load_taxonomy  # noqa: E402
from tools.make_demo_class import build  # noqa: E402


def digest_dir(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.glob("*.json")):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def run(students: int, weeks: int, seed: int, keep: bool) -> int:
    tax = load_taxonomy()
    root = Path(tempfile.mkdtemp(prefix="redo_stress_"))
    try:
        t0 = time.perf_counter()
        build(root / "a", seed, weeks=weeks, n_students=students, backups=False)
        t1 = time.perf_counter()
        build(root / "b", seed, weeks=weeks, n_students=students, backups=False)
        files = sorted((root / "a").glob("*.json"))
        total_m = total_att = 0
        problems: list[str] = []
        for f in files:
            doc = load(f)
            total_m += len(doc["mistakes"])
            total_att += sum(len(m["attempts"]) for m in doc["mistakes"])
            problems += [f"{f.name} {p}" for p in check_invariants(doc, tax)]
        size = sum(f.stat().st_size for f in files)
        same = digest_dir(root / "a") == digest_dir(root / "b")
        print(f"学生 {students} × {weeks} 周（seed {seed}）")
        print(f"生成耗时 {t1 - t0:.1f}s，每学生 {(t1 - t0) / students * 1000:.0f}ms")
        print(f"错题 {total_m} 道，重做记录 {total_att} 条，文件合计 {size / 1024:.0f} KB")
        print(f"不变量审计：{'通过' if not problems else f'{len(problems)} 处违规'}")
        for p in problems[:20]:
            print("  " + p)
        print(f"同 seed 双跑逐字节一致：{'是' if same else '否'}")
        if keep:
            dst = Path("out") / f"stress_{students}x{weeks}"
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(root / "a", dst)
            print(f"数据保留在 {dst}")
        return 0 if (not problems and same) else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.students, a.weeks, a.seed, a.keep))
