"""错因建议评测：同一份标注集上比较 关键词基线 与 AI（按提示词版本）。

用法：
  python tools/eval_suggest.py --mode baseline --split all
  python tools/eval_suggest.py --mode ai --prompt v1 --split all --workers 6

输出：终端摘要 + evals/results/<日期>_<mode>[_<prompt>].json（逐条结果，供 bad case 复盘）。
口径见 evals/README.md。评测集为作者构造与标注，不是真实学生原话。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redo_right import ai  # noqa: E402
from redo_right.taxonomy import load_taxonomy  # noqa: E402

DATASET = ROOT / "evals" / "cause_suggestion.jsonl"
RESULTS = ROOT / "evals" / "results"


def load_items(path: Path = DATASET, split: str = "all") -> list[dict]:
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return items if split == "all" else [i for i in items if i["split"] == split]


# 评测侧的泄露检测，独立于产品合同：产品合同改了，这里不跟着改，避免“自己给自己打分”。
# 规则型：查反问里有没有学生原话（含题目引用）中没出现过的数字、方法名，或明确的解题信号。
# 查不出用文字纠正概念的泄露（例如“1 不是质数吗？”），README 里写明。
EVAL_METHOD_TERMS = ("换元", "构造", "归纳", "配方", "均值", "柯西", "抽屉", "对称", "复数", "参数", "反证",
                     "数形结合", "待定系数", "判别式", "韦达", "裂项", "放缩", "辅助线", "坐标系", "累加", "累乘", "特殊值")
EVAL_SOLUTION_SIGNALS = ("答案是", "解：", "解法如下", "步骤如下", "=", "所以结果", "应该是", "正确的是")


def leak_detail(question: str, note: str, problem: str) -> list[str]:
    q, src = question or "", f"{note} {problem}"
    out = [f"数字 {n}" for n in dict.fromkeys(re.findall(r"\d+(?:\.\d+)?", q)) if n not in src]
    out += [f"方法 {t}" for t in EVAL_METHOD_TERMS if t in q and t not in src]
    out += [f"信号 {s}" for s in EVAL_SOLUTION_SIGNALS if s in q and s not in src]
    return out


def _leaked(question: str) -> bool:
    return any(sig in (question or "") for sig in ai.SOLUTION_SIGNALS)


def run_item(item: dict, mode: str, prompt: str, taxonomy, call_fn=None) -> dict:
    calls: list[float] = []
    if mode == "baseline":
        t0 = time.perf_counter()
        sg = ai.deterministic_suggestion(item["note"], item["problem"], taxonomy)
        calls.append(time.perf_counter() - t0)
    else:
        base_fn = call_fn or ai.http_call

        def timed(messages):
            t0 = time.perf_counter()
            try:
                return base_fn(messages)
            finally:
                calls.append(time.perf_counter() - t0)

        sg = ai.suggest_cause(item["problem"], item["note"], taxonomy, call_fn=timed, prompt_version=prompt)
    gold, acceptable = item["gold"], item["acceptable"]
    return {
        "id": item["id"], "split": item["split"], "category": item["category"], "note": item["note"],
        "gold": gold, "acceptable": acceptable,
        "code": sg.code, "confidence": sg.confidence, "source": sg.source,
        "evidence_quote": sg.evidence_quote, "question": sg.question,
        "violations": sg.violations, "calls": len(calls), "latency_s": round(sum(calls), 3),
        "strict": (sg.code == gold) if gold else None,
        "lenient": (sg.code in acceptable) if gold else None,
        "leak_detail": leak_detail(sg.question, item["note"], item["problem"]),
        "leaked": bool(leak_detail(sg.question, item["note"], item["problem"])),
        "intercepted": sum(1 for v in sg.violations if ("解题" in v or "数字" in v or "方法" in v)),
        "null_ok": (sg.code is None) if gold is None else None,
        "overclaim": (gold is None and sg.code is not None and sg.confidence == "high"),
    }


def run(items: list[dict], mode: str, prompt: str = ai.PROMPT_VERSION, workers: int = 6, call_fn=None) -> list[dict]:
    tax = load_taxonomy()
    if mode == "baseline" or workers <= 1:
        return [run_item(i, mode, prompt, tax, call_fn) for i in items]
    lock = threading.Lock()
    done = [0]

    def one(i):
        r = run_item(i, mode, prompt, tax, call_fn)
        with lock:
            done[0] += 1
            print(f"\r  {done[0]}/{len(items)}", end="", file=sys.stderr, flush=True)
        return r

    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(one, items))
    print(file=sys.stderr)
    return out


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def score(results: list[dict]) -> dict:
    labelled = [r for r in results if r["gold"]]
    by_cat = defaultdict(list)
    for r in labelled:
        by_cat[r["category"]].append(r["strict"])
    by_conf = defaultdict(list)
    for r in labelled:
        if r["code"]:
            by_conf[r["confidence"]].append(r["strict"])
    ai_rows = [r for r in results if r["calls"] and r["source"] in ("ai", "deterministic") and r.get("calls", 0) > 0]
    is_ai = any(r["source"] == "ai" for r in results) or any(r["violations"] for r in results)
    first_try = [r["source"] == "ai" and not r["violations"] for r in results]
    retried_ok = [r["source"] == "ai" and bool(r["violations"]) for r in results]
    fallback = [r["source"] == "deterministic" for r in results]
    lat = [r["latency_s"] for r in results if r["calls"]]
    confusion = Counter((r["gold"], r["code"]) for r in labelled if not r["strict"])
    return {
        "n": len(results),
        "n_labelled": len(labelled),
        "strict_accuracy": _rate([r["strict"] for r in labelled]),
        "lenient_accuracy": _rate([r["lenient"] for r in labelled]),
        "abstain_rate": _rate([r["code"] is None for r in labelled]),
        "by_category": {k: _rate(v) for k, v in sorted(by_cat.items())},
        "accuracy_by_confidence": {k: {"n": len(v), "acc": _rate(v)} for k, v in sorted(by_conf.items())},
        "contract_first_try": _rate(first_try) if is_ai else None,
        "contract_retry_pass": _rate(retried_ok) if is_ai else None,
        "fallback_rate": _rate(fallback) if is_ai else None,
        "leaked": sum(r["leaked"] for r in results),
        "intercepted": sum(r.get("intercepted", 0) for r in results),
        "no_basis_null": _rate([r["null_ok"] for r in results if r["gold"] is None]),
        "overclaim": sum(r["overclaim"] for r in results),
        "latency_p50_s": round(statistics.median(lat), 2) if lat and is_ai else None,
        "latency_p95_s": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 2) if lat and is_ai else None,
        "top_confusions": [f"{g}→{c}×{n}" for (g, c), n in confusion.most_common(6)],
        "_unused": len(ai_rows),
    }


def summarize(results: list[dict]) -> dict:
    out = {"all": score(results)}
    for sp in ("dev", "test"):
        rs = [r for r in results if r["split"] == sp]
        if rs:
            out[sp] = score(rs)
    for v in out.values():
        v.pop("_unused", None)
    return out


def render(summary: dict, label: str) -> str:
    lines = [f"## {label}", "", "| 指标 | " + " | ".join(summary) + " |", "|---|" + "---|" * len(summary)]
    keys = ["n_labelled", "strict_accuracy", "lenient_accuracy", "abstain_rate", "contract_first_try",
            "contract_retry_pass", "fallback_rate", "leaked", "intercepted", "no_basis_null", "overclaim",
            "latency_p50_s", "latency_p95_s"]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(summary[s].get(k)) for s in summary) + " |")
    lines.append(f"| by_category | " + " | ".join(json.dumps(summary[s]["by_category"], ensure_ascii=False) for s in summary) + " |")
    lines.append(f"| by_confidence | " + " | ".join(json.dumps(summary[s]["accuracy_by_confidence"], ensure_ascii=False) for s in summary) + " |")
    lines.append(f"| top_confusions | " + " | ".join("、".join(summary[s]["top_confusions"]) or "—" for s in summary) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("baseline", "ai"), required=True)
    ap.add_argument("--prompt", default=ai.PROMPT_VERSION)
    ap.add_argument("--split", choices=("dev", "test", "all"), default="all")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--tag", default="", help="结果文件名后缀，区分同一提示词在不同合同下的运行")
    a = ap.parse_args(argv)
    if a.mode == "ai" and not ai.api_configured():
        print("未配置 API key（REDO_RIGHT_API_KEY 或 DEEPSEEK_API_KEY），无法跑 AI 评测", file=sys.stderr)
        return 2
    items = load_items(split=a.split)
    t0 = time.perf_counter()
    results = run(items, a.mode, a.prompt, a.workers)
    summary = summarize(results)
    label = f"{a.mode}" + (f" · prompt {a.prompt}" if a.mode == "ai" else "") + f" · split {a.split} · {date.today().isoformat()}"
    print(render(summary, label))
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    if not a.no_save:
        RESULTS.mkdir(parents=True, exist_ok=True)
        name = (f"{date.today().isoformat()}_{a.mode}" + (f"_{a.prompt}" if a.mode == "ai" else "")
                + (f"_{a.tag}" if a.tag else "") + ".json")
        (RESULTS / name).write_text(
            json.dumps({"label": label, "summary": summary, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"逐条结果：evals/results/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
