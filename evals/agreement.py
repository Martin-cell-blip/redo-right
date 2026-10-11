"""第二标注者一致性：作者 gold 与独立盲标（evals/blind_labels_*.json）之间的 Cohen's κ，并交叉模型错误。

    python evals/agreement.py [--blind evals/blind_labels_sonnet_2026-10-10.json] [--run results/<file>.json]

默认 --run 为 2026-10-06_ai_v2_new-contract.json；results/agreement.json 由默认参数生成。

类别 = C1..C6 + null（看不出错因），共 7 类；κ 按 7 类算，另给"是否留空"的二分 κ。
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent
CATS = ["C1", "C2", "C3", "C4", "C5", "C6", None]


def kappa(a: list, b: list) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blind", default=str(ROOT / "blind_labels_sonnet_2026-10-10.json"))
    ap.add_argument("--run", default=str(ROOT / "results" / "2026-10-06_ai_v2_new-contract.json"))
    a = ap.parse_args(argv)

    items = [json.loads(l) for l in (ROOT / "cause_suggestion.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    blind = json.loads(pathlib.Path(a.blind).read_text(encoding="utf-8"))
    run = {r["id"]: r for r in json.loads(pathlib.Path(a.run).read_text(encoding="utf-8"))["results"]}

    gold = [it["gold"] for it in items]
    missing = [it["id"] for it in items if it["id"] not in blind]
    if missing:
        raise SystemExit(f"盲标文件缺少 {len(missing)} 个 id：{missing[:5]}…（缺失不能当作留空）")
    second = [blind[it["id"]] for it in items]
    acceptable = [set(it.get("acceptable") or []) | ({it["gold"]} if it["gold"] else set()) for it in items]

    agree = sum(g == s for g, s in zip(gold, second))
    lenient = sum((s in acc) if s else (g is None) for g, s, acc in zip(gold, second, acceptable))
    k7 = kappa(gold, second)
    k_null = kappa([g is None for g in gold], [s is None for s in second])

    disagreements = [(it["id"], it["split"], it["category"], g, s, run[it["id"]]["code"], run[it["id"]]["strict"])
                     for it, g, s in zip(items, gold, second) if g != s]
    model_wrong = [it["id"] for it in items if it["gold"] is not None and not run[it["id"]]["strict"]]
    dis_ids = {d[0] for d in disagreements}
    overlap = [i for i in model_wrong if i in dis_ids]

    print(f"条目 {len(items)}；严格一致 {agree}/{len(items)} = {agree/len(items):.3f}；"
          f"宽松一致（第二标注在可接受集合内）{lenient}/{len(items)} = {lenient/len(items):.3f}")
    print(f"Cohen's κ（7 类）= {k7:.3f}；是否留空的二分 κ = {k_null:.3f}")
    print(f"作者留空 {sum(g is None for g in gold)} 条，盲标留空 {sum(s is None for s in second)} 条")
    print(f"\n不一致 {len(disagreements)} 条（id / split / 类别 / 作者 / 盲标 / 模型 / 模型严格对）：")
    for d in disagreements:
        print("  ", " / ".join(str(x) for x in d))
    print(f"\n模型（{pathlib.Path(a.run).name}）错 {len(model_wrong)} 条：{model_wrong}；其中落在标注不一致条目上的：{overlap}")
    out = {"n": len(items), "strict_agree": agree, "lenient_agree": lenient, "kappa_7": k7, "kappa_null": k_null,
           "disagreements": [dict(zip(("id", "split", "category", "gold", "second", "model", "model_strict"), d)) for d in disagreements],
           "model_wrong": model_wrong, "model_wrong_on_disagreement": overlap, "blind_file": pathlib.Path(a.blind).name,
           "run_file": pathlib.Path(a.run).name}
    (ROOT / "results" / "agreement.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
