"""不确定性量化：把 evals/results/*.json 里的逐条结果汇总成置信区间、跨轮波动与配对比较。

用法（仓库根目录）：
    python evals/uncertainty.py            # 写 evals/UNCERTAINTY.md 与 evals/results/uncertainty.json
    python evals/uncertainty.py --seed 7   # 换 bootstrap 种子

只读结果文件，不调模型。严格准确率只算有 gold 的条目（与 eval_suggest.py 的 n_labelled 一致）。
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import re
import statistics
from collections import defaultdict
from itertools import combinations

ROOT = pathlib.Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FNAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<mode>baseline|ai)(?:_(?P<prompt>v\d+))?(?:_(?P<tag>.+))?\.json$")


# ---------- 统计工具 ----------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def binom_two_sided(k: int, n: int) -> float:
    """精确 McNemar：不一致对里一方赢 k 次、共 n 次，p = 0.5 下的双侧概率。"""
    if n == 0:
        return float("nan")
    lo = min(k, n - k)
    p = sum(math.comb(n, i) for i in range(0, lo + 1)) / 2 ** n
    return min(1.0, 2 * p)


def fmt(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if isinstance(x, float):
        return f"{x:.3f}"
    return str(x)


# ---------- 读入 ----------

def family_of(name: str) -> dict | None:
    m = FNAME.match(name)
    if not m:
        return None
    d = m.groupdict()
    tag = d["tag"] or ""
    rep = re.search(r"-r(\d+)$", tag)
    base_tag = re.sub(r"-r\d+$", "", tag)
    model = "gpt-4.1-mini" if "gpt-4.1-mini" in base_tag else ("deepseek-chat" if d["mode"] == "ai" else "rule")
    if d["mode"] == "baseline":
        fam = "baseline"
    else:
        fam = f"{d['prompt']} · {base_tag or 'default'} · {model}"
    return {"family": fam, "model": model, "prompt": d["prompt"], "tag": base_tag,
            "rep": int(rep.group(1)) if rep else 1, "date": d["date"], "mode": d["mode"]}


def load_runs() -> list[dict]:
    runs = []
    for f in sorted(RESULTS.glob("*.json")):
        meta = family_of(f.name)
        if not meta:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        rows = {r["id"]: r for r in data["results"]}
        runs.append({**meta, "file": f.name, "rows": rows})
    return runs


# ---------- 单轮：置信区间 ----------

def split_rows(rows: dict, split: str) -> list[dict]:
    return [r for r in rows.values() if split == "all" or r["split"] == split]


def run_ci(run: dict) -> dict:
    out = {}
    for sp in ("all", "dev", "test"):
        rs = [r for r in split_rows(run["rows"], sp) if r["gold"] is not None]
        k = sum(1 for r in rs if r["strict"])
        lo, hi = wilson(k, len(rs))
        cats = defaultdict(list)
        for r in rs:
            cats[r["category"]].append(r["strict"])
        out[sp] = {
            "n": len(rs), "k": k, "acc": k / len(rs) if rs else float("nan"), "ci": (lo, hi),
            "by_category": {c: (sum(v), len(v)) for c, v in sorted(cats.items())},
        }
        highs = [r["strict"] for r in rs if r.get("confidence") == "high"]
        if highs:
            out[sp]["high"] = (sum(highs), len(highs), wilson(sum(highs), len(highs)))
    return out


# ---------- 家族：跨轮波动与条目稳定性 ----------

def family_stats(runs: list[dict], split: str) -> dict:
    ids = sorted({i for run in runs for i, r in run["rows"].items()
                  if (split == "all" or r["split"] == split) and r["gold"] is not None})
    per_item = {i: [run["rows"][i]["strict"] for run in runs if i in run["rows"]] for i in ids}
    accs = [sum(run["rows"][i]["strict"] for i in ids) / len(ids) if ids else float("nan") for run in runs]
    always_wrong = [i for i, v in per_item.items() if v and not any(v)]
    unstable = [i for i, v in per_item.items() if len(v) > 1 and any(v) and not all(v)]
    return {"n_items": len(ids), "n_runs": len(runs), "accs": accs,
            "mean": statistics.mean(accs), "min": min(accs), "max": max(accs),
            "always_wrong": always_wrong, "unstable": unstable, "per_item": per_item}


# ---------- 家族对：配对比较 ----------

def pair_compare(a: dict, b: dict, split: str, seed: int, n_boot: int = 10000) -> dict:
    """a、b 为 family_stats 输出。按条目配对：每条取各家族跨轮的正确率，再 bootstrap 条目。"""
    ids = sorted(set(a["per_item"]) & set(b["per_item"]))
    pa = {i: statistics.mean(float(x) for x in a["per_item"][i]) for i in ids}
    pb = {i: statistics.mean(float(x) for x in b["per_item"][i]) for i in ids}
    diff = statistics.mean(pb[i] - pa[i] for i in ids)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [ids[rng.randrange(len(ids))] for _ in ids]
        boots.append(statistics.mean(pb[i] - pa[i] for i in sample))
    boots.sort()
    lo, hi = boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]
    # 多数票下的不一致对（精确 McNemar）
    ma = {i: statistics.mean(a["per_item"][i]) >= 0.5 for i in ids}
    mb = {i: statistics.mean(b["per_item"][i]) >= 0.5 for i in ids}
    b_only = [i for i in ids if mb[i] and not ma[i]]
    a_only = [i for i in ids if ma[i] and not mb[i]]
    return {"n": len(ids), "diff": diff, "ci": (lo, hi), "b_only": b_only, "a_only": a_only,
            "mcnemar_p": binom_two_sided(len(b_only), len(b_only) + len(a_only))}


# ---------- 报告 ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261010)
    a = ap.parse_args(argv)

    runs = load_runs()
    if not runs:
        print("results/ 下没有结果文件")
        return 1
    fams: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        fams[r["family"]].append(r)

    md = ["# 不确定性量化", "",
          f"由 `evals/uncertainty.py` 生成（bootstrap 种子 {a.seed}，10,000 次重采样）。"
          "严格准确率只算有 gold 的条目；区间为 Wilson 95%；配对比较按条目 bootstrap，"
          "并对多数票下的不一致对做精确 McNemar 检验。", ""]

    # 1. 每轮置信区间
    md += ["## 1. 每轮严格准确率与 95% 区间", "",
           "| 结果文件 | 家族 | all | dev | test | test high 档 |", "|---|---|---|---|---|---|"]
    per_run = {}
    for run in runs:
        ci = run_ci(run)
        per_run[run["file"]] = ci
        cells = []
        for sp in ("all", "dev", "test"):
            c = ci[sp]
            cells.append(f"{c['k']}/{c['n']} = {fmt(c['acc'])} [{fmt(c['ci'][0])}, {fmt(c['ci'][1])}]")
        h = ci["test"].get("high")
        hs = f"{h[0]}/{h[1]} = {fmt(h[0]/h[1])} [{fmt(h[2][0])}, {fmt(h[2][1])}]" if h else "—"
        md.append(f"| `{run['file']}` | {run['family']} | " + " | ".join(cells) + f" | {hs} |")
    md.append("")

    # 2. test 分类别样本量
    md += ["## 2. 留出集按类别的样本量（看清哪些格子只有一两条）", ""]
    any_run = runs[-1]
    cats = run_ci(any_run)["test"]["by_category"]
    md += ["| 类别 | test 有 gold 条数 |", "|---|---|"]
    md += [f"| {c} | {n} |" for c, (k, n) in cats.items()]
    md += ["", "类别准确率里 0.0 或 1.0 若只对应 1–2 条，不能解读为该类别的能力。", ""]

    # 3. 家族跨轮
    md += ["## 3. 同一配置多轮之间的波动", "",
           "| 家族 | 轮数 | test 条数 | test 各轮 | 均值 | 极差 | 各轮全错（多轮家族可读作系统性；单轮家族只是一次观察） | 时对时错（采样） |",
           "|---|---|---|---|---|---|---|---|"]
    fstats = {}
    for fam, rs in fams.items():
        st = family_stats(rs, "test")
        fstats[fam] = st
        md.append(f"| {fam} | {st['n_runs']} | {st['n_items']} | "
                  + "、".join(fmt(x) for x in st["accs"]) + f" | {fmt(st['mean'])} | {fmt(st['max']-st['min'])} | "
                  + ("、".join(st["always_wrong"]) or "—") + " | " + ("、".join(st["unstable"]) or "—") + " |")
    md.append("")

    # 4. 配对比较
    md += ["## 4. 配对比较（test，按条目 bootstrap；正数表示后者更高）", "",
           "| 比较 | 条数 | 准确率差 | 95% bootstrap 区间 | 只后者对 | 只前者对 | 精确 McNemar p |",
           "|---|---|---|---|---|---|---|"]
    pairs = []
    names = list(fstats)
    for x, y in combinations(names, 2):
        pairs.append((x, y))
    pair_out = {}
    for x, y in pairs:
        pc = pair_compare(fstats[x], fstats[y], "test", a.seed)
        pair_out[f"{x} → {y}"] = pc
        md.append(f"| {x} → {y} | {pc['n']} | {fmt(pc['diff'])} | [{fmt(pc['ci'][0])}, {fmt(pc['ci'][1])}] | "
                  + ("、".join(pc["b_only"]) or "—") + " | " + ("、".join(pc["a_only"]) or "—") + f" | {fmt(pc['mcnemar_p'])} |")
    md.append("")

    # 5. 读法
    md += ["## 5. 怎么读这些数", "",
           "- 区间宽是样本量的直接后果：留出集 25 条有 gold 的条目，一条就是 4 个百分点。"
           "两个区间重叠不等于没有差异，只等于这份数据分不出差异。",
           "- 多轮家族里「各轮全错」的条目是系统性失败（提示词或标注本身两可），「时对时错」的条目是采样噪声；单轮家族的这一列只是一次观察，不能推断系统性；"
           "两者在准确率里看起来一样，处理方式完全不同。",
           "- 配对比较比各自的区间更有力：同一条题两个版本都对或都错不提供信息，只有不一致对才算数。"
           "不一致对只有一两个时，p 值不可能小（1 个不一致对 p = 1.0，2 个全偏向一方 p = 0.5），这是数据的限制，不是版本没有差别的证据；不一致对达到 6 个且全偏向一方时 p 才会低于 0.05。",
           "- 跨模型比较只在这 84 条、这份提示词、这份合同上成立；换任务分布结论不迁移。",
           "- 结果文件的 label 不含模型名，家族里的模型信息来自文件名，对照表见 `results/MANIFEST.md`。", ""]

    (ROOT / "UNCERTAINTY.md").write_text("\n".join(md), encoding="utf-8")
    payload = {"seed": a.seed, "per_run": per_run,
               "families": {k: {kk: vv for kk, vv in v.items() if kk != "per_item"} for k, v in fstats.items()},
               "pairs": pair_out}
    (RESULTS / "uncertainty.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=list), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
