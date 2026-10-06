"""生成一个合成演示班级（12 人 × 3 周），只走产品自己的 Service，保证数据服从全部产品规则。

⚠️ 合成数据，供演示热力图 / 重练单 / 物料清单。不是真实学生，不得作为效果证据。
用法：python tools/make_demo_class.py examples/demo_class [--seed 20261006]
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redo_right.service import Service, ServiceError  # noqa: E402

NAMES = ["陈思远", "李若曦", "王子墨", "张一诺", "刘宇航", "杨梓涵",
         "赵欣怡", "周沐阳", "吴启晨", "徐佳宁", "孙浩然", "马泽楷"]
TOPICS = {
    "数列-递推": {"C2": 0.5, "C1": 0.2, "C3": 0.3},
    "函数-单调性": {"C1": 0.45, "C4": 0.3, "C6": 0.25},
    "不等式-均值": {"C2": 0.35, "C3": 0.35, "C6": 0.3},
    "解析几何-直线与圆": {"C3": 0.6, "C5": 0.25, "C4": 0.15},
    "组合-计数原理": {"C4": 0.5, "C2": 0.3, "C1": 0.2},
    "三角-恒等变换": {"C3": 0.4, "C1": 0.3, "C5": 0.3},
}
NOTES = {
    "C1": ["定理条件记反了", "忘了这个公式", "概念混了"],
    "C2": ["没想到换元", "不知道怎么下手", "想到的方法太绕"],
    "C3": ["符号算错", "抄错数", "最后一步算错"],
    "C4": ["看漏了偶数条件", "把所求看错", "题意理解反了"],
    "C5": ["没时间了", "做得太慢"],
    "C6": ["步骤跳了被扣分", "没写取等条件"],
}
PASS_RATE = {"C1": 0.55, "C2": 0.5, "C3": 0.7, "C4": 0.65, "C5": 0.7, "C6": 0.75}


def pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def build(out_dir: Path, seed: int, start: date = date(2026, 9, 14), weeks: int = 3) -> None:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(NAMES):
        clock = {"d": start}
        ids = iter(f"{i + 1:02d}{k:03d}" for k in range(1, 1000))
        svc = Service(out_dir / f"{name}.json", today_fn=lambda: clock["d"], id_fn=lambda: next(ids))
        svc.init(name, "高一", [1, 7, 21])
        diligence = rng.uniform(0.45, 0.95)  # 这个学生到期后真的去重做的概率
        for day in range(weeks * 7):
            clock["d"] = start + timedelta(days=day)
            # 到期的题：按勤奋度决定做不做；做了按错因通过率决定对错
            for m in svc.due():
                if rng.random() > diligence:
                    continue
                passed = rng.random() < PASS_RATE[m.cause]
                secs = rng.randint(240, 900) if svc.taxonomy.get(m.cause).redo_mode == "timed" else None
                svc.redo(m.id, passed, seconds=secs, note="" if passed else rng.choice(NOTES[m.cause]))
            # 二次错的题：一半概率在当天重新归因（学生自己想清楚了）
            for m in svc.pending_retag():
                if rng.random() < 0.5:
                    new = pick(rng, TOPICS[m.topic])
                    svc.retag(m.id, new, "重做两次都错，重新想了一遍错因", by="student")
            # 周一到周五每天 0–2 道新错题（作业/讲义），周日模考 0–3 道
            wd = clock["d"].weekday()
            n_new = rng.choice([0, 1, 1, 2]) if wd < 5 else (rng.choice([0, 2, 3]) if wd == 6 else 0)
            for _ in range(n_new):
                topic = rng.choice(list(TOPICS))
                cause = pick(rng, TOPICS[topic])
                src = "模考" if wd == 6 else rng.choice(["作业", "讲义"])
                ref = f"{src} W{day // 7 + 1} 第 {rng.randint(1, 20)} 题"
                svc.add(topic, cause, ref, src, rng.choice(NOTES[cause]))
    (out_dir / "DEMO_DATA_NOTICE.md").write_text(
        f"本文件夹内 {len(NAMES)} 份学生数据为**合成演示数据**（tools/make_demo_class.py，seed={seed}，"
        f"起始日 {start.isoformat()}，{weeks} 周）。不是真实学生，不得作为任何教学效果证据。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--seed", type=int, default=20261006)
    a = ap.parse_args()
    build(Path(a.out_dir), a.seed)
    print(f"demo class written to {a.out_dir}")
