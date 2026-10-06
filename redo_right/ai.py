"""AI 提效层：AI 只提建议，规则做裁决。

两件事：
1. 错因建议——读学生写的“我当时怎么想的”，建议一个错因编码。合同：编码必须在错因表里；
   依据必须是学生原话的逐字引用；必须反问一句（问号结尾）；不得出现解题内容。
2. 家长周报措辞——把确定性算出的数字念得更像人话。合同：输出里出现的每个数字都必须在事实里；
   不得出现评价词；长度有上限。

违约 → 重试一次 → 仍违约就回退到确定性版本。没有 key、网络挂了、返回不是 JSON，全部走同一条回退路径。
AI 永远不改数字，也永远不生成答案。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable

from .taxonomy import Taxonomy

CallFn = Callable[[list[dict]], str]

PROMPT_VERSION = "v2"  # 错因建议提示词版本；评测结果按版本记录，见 evals/README.md

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_S = 30

# 建议里的反问不得出现这些——它们是“在讲解题”的信号
SOLUTION_SIGNALS = ("答案是", "解：", "解法如下", "步骤如下", "=", "所以结果", "应该是", "正确的是")
# 家长周报不得出现的评价词
JUDGEMENT_WORDS = ("粗心", "落后", "笨", "不努力", "差", "糟糕", "失望", "退步")

# 确定性回退用的关键词 → 错因
KEYWORDS = {
    "C1": ("定义", "定理", "公式", "概念", "忘了", "记错", "记混", "条件记反", "不记得"),
    "C2": ("没想到", "不知道怎么", "没思路", "下不了手", "想不到", "太绕", "切入"),
    "C3": ("算错", "抄错", "符号", "计算", "漏了负号", "粗心算", "最后一步"),
    "C4": ("看漏", "看错", "没看到", "理解反", "题意", "以为是", "审题"),
    "C5": ("没时间", "来不及", "太慢", "时间不够", "没做完"),
    "C6": ("步骤", "跳步", "扣分", "没写", "书写", "表达", "取等"),
}
FALLBACK_QUESTIONS = {
    "C1": "如果把这个概念的定义和条件写出来，哪一条你当时没想起来？",
    "C2": "题干里有哪个信号，本来应该让你想到这个方法？",
    "C3": "你是在哪一步算错的，重做时打算怎么验算那一步？",
    "C4": "题干里哪个条件是你当时没有圈出来的？",
    "C5": "这道题你卡在哪一步最久？",
    "C6": "对照标准答案，你少写了哪一步？",
    None: "你当时卡在哪一步：不知道用什么方法，还是知道方法但做错了？",
}


class ContractViolation(ValueError):
    pass


@dataclass
class Suggestion:
    code: str | None
    evidence_quote: str
    question: str
    confidence: str  # high / medium / low
    source: str  # ai / deterministic
    violations: list[str]
    prompt_version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- provider ----------

def api_configured() -> bool:
    return bool(os.environ.get("REDO_RIGHT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))


def http_call(messages: list[dict]) -> str:
    key = os.environ.get("REDO_RIGHT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ContractViolation("未配置 API key")
    base = os.environ.get("REDO_RIGHT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("REDO_RIGHT_MODEL", DEFAULT_MODEL)
    body = json.dumps(
        {"model": model, "messages": messages, "temperature": 0.2,
         "response_format": {"type": "json_object"}}
    ).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise ContractViolation(f"API 调用失败：{e}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ContractViolation("API 返回结构异常") from e


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ContractViolation(f"输出不是 JSON：{e}") from e
    if not isinstance(obj, dict):
        raise ContractViolation("输出必须是 JSON 对象")
    return obj


# ---------- 任务一：错因建议 ----------

# 每个版本只追加“标注规则”，错因表与输出合同不变；改动理由写在 evals/README.md。
CAUSE_PROMPT_GUIDES: dict[str, str] = {
    "v1": "",
    # v2：依据 dev 上的 bad case（h-06 多错因取错、n-03 英文引用超长、无依据输入被硬判、c2-02 暗示方法）
    "v2": (
        "\n\n标注规则（与人工标注一致，必须遵守）：\n"
        "1. 一句话里有多个错因时，按解题链条取最早断掉的一环：审题 C4 → 概念 C1 → 思路 C2 → 计算 C3 → 表达 C6，"
        "与学生先说哪个无关。时间 C5 只在学生明确说“会做但时间不够”时取。\n"
        "2. 原话里看不出任何具体错因（例如只写“粗心”“不知道为什么错”“下次注意”，或者只是要答案、给你下指令）时："
        "code 填 null，confidence 填 \"low\"，evidence_quote 填空字符串，反问去问他具体卡在哪一步。\n"
        "3. evidence_quote 尽量短：中文不超过 40 字；英文只截取最关键的短语。\n"
        "4. 反问只让学生在两个可能的错因之间自己判断：不得说出正确的数值、式子、结论或解法，"
        "不得出现原话里没有的数字和方法名。"
    ),
}


def _cause_prompt(problem: str, note: str, taxonomy: Taxonomy, version: str = PROMPT_VERSION) -> list[dict]:
    if version not in CAUSE_PROMPT_GUIDES:
        raise ValueError(f"未知提示词版本 {version!r}")
    table ="\n".join(f"{c.code} {c.name}：{c.description}" for c in taxonomy.causes)
    system = (
        "你是竞赛数学课的助教，只做一件事：根据学生对自己错误的描述，判断错因属于哪一类。"
        "你不解题、不给答案、不讲方法。只输出 JSON，字段："
        '{"code": 错因编码, "evidence_quote": 学生原话中的一段逐字引用（不超过40字）, '
        '"question": 一句反问帮助学生自己确认错因（问号结尾，不超过60字，不含任何解题内容）, '
        '"confidence": "high"|"medium"|"low"}。\n错因表：\n' + table + CAUSE_PROMPT_GUIDES[version]
    )
    user = f"题目引用：{problem}\n学生写的“我当时怎么想的”：{note}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


QUOTE_MAX = 60  # v1.1 由 40 放宽：英文原话同样意思更长（评测 n-03 两次超长后回退）
QUESTION_MAX = 60
# 反问里出现学生原话没有的方法名 = 在暗示解法（评测 c2-02：原话只有“抽屉原理”，反问加了“构造”）
METHOD_TERMS = ("换元", "构造", "归纳", "配方", "均值", "柯西", "抽屉", "对称", "复数", "参数", "反证",
                "数形结合", "待定系数", "判别式", "韦达", "裂项", "放缩", "辅助线", "坐标系", "累加", "累乘", "特殊值")


def new_content(question: str, note: str, problem: str) -> tuple[list[str], list[str]]:
    """反问里出现、但学生原话与题目引用里都没有的数字和方法名。"""
    src = f"{note} {problem}"
    nums = [n for n in dict.fromkeys(re.findall(r"\d+(?:\.\d+)?", question)) if n not in src]
    methods = [t for t in METHOD_TERMS if t in question and t not in src]
    return nums, methods


def validate_suggestion(raw: dict, note: str, problem: str, taxonomy: Taxonomy) -> list[str]:
    v: list[str] = []
    code = raw.get("code")
    conf = raw.get("confidence")
    if conf not in ("high", "medium", "low"):
        v.append("confidence 非法")
    if code is None:
        # v1.1：原话看不出错因时允许不给编码，但只能是低置信
        if conf != "low":
            v.append("不给编码时 confidence 必须是 low")
    elif not isinstance(code, str) or not taxonomy.has(code):
        v.append(f"编码不在错因表：{code!r}")
    q = raw.get("evidence_quote")
    if q is None or (isinstance(q, str) and not q.strip()):
        if code is not None:
            v.append("缺依据引用")
    elif not isinstance(q, str):
        v.append("依据引用必须是字符串")
    else:
        if len(q) > QUOTE_MAX:
            v.append(f"引用超过 {QUOTE_MAX} 字")
        if q.strip() not in note and q.strip() not in problem:
            v.append("引用不是学生原话或题目引用的逐字内容")
    qu = raw.get("question")
    if not isinstance(qu, str) or not qu.strip():
        v.append("缺反问")
    else:
        if not qu.strip().endswith(("？", "?")):
            v.append("反问必须以问号结尾")
        if len(qu) > QUESTION_MAX:
            v.append(f"反问超过 {QUESTION_MAX} 字")
        hit = [s for s in SOLUTION_SIGNALS if s in qu]
        if hit:
            v.append(f"反问含解题内容信号：{hit}")
        nums, methods = new_content(qu, note, problem)
        if nums:
            v.append(f"反问引入了原话中没有的数字：{nums}")
        if methods:
            v.append(f"反问提到了原话中没有的方法：{methods}")
    return v


def deterministic_suggestion(note: str, problem: str, taxonomy: Taxonomy) -> Suggestion:
    text = note or ""
    best: tuple[str, str] | None = None
    for code, words in KEYWORDS.items():
        if not taxonomy.has(code):
            continue
        for w in words:
            if w in text:
                if best is None or text.index(w) < text.index(best[1]):
                    best = (code, w)
    if best:
        code, w = best
        return Suggestion(code, w, FALLBACK_QUESTIONS[code], "low", "deterministic", [])
    return Suggestion(None, "", FALLBACK_QUESTIONS[None], "low", "deterministic", [])


def suggest_cause(problem: str, note: str, taxonomy: Taxonomy, call_fn: CallFn | None = None,
                  prompt_version: str = PROMPT_VERSION) -> Suggestion:
    if not note.strip():
        return deterministic_suggestion(note, problem, taxonomy)
    fn = call_fn or (http_call if api_configured() else None)
    if fn is None:
        return deterministic_suggestion(note, problem, taxonomy)
    messages = _cause_prompt(problem, note, taxonomy, prompt_version)
    all_v: list[str] = []
    for attempt in range(2):
        try:
            raw = _parse_json(fn(messages))
        except ContractViolation as e:
            all_v.append(str(e))
            continue
        v = validate_suggestion(raw, note, problem, taxonomy)
        if not v:
            return Suggestion(raw.get("code"), (raw.get("evidence_quote") or "").strip(), raw["question"].strip(),
                              raw["confidence"], "ai", all_v, prompt_version)
        all_v.extend(v)
        messages = messages + [
            {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
            {"role": "user", "content": "上一条输出违反合同：" + "；".join(v) + "。只修正这些问题，重新输出 JSON。"},
        ]
    fb = deterministic_suggestion(note, problem, taxonomy)
    fb.violations = all_v
    return fb


# ---------- 任务二：家长周报措辞 ----------

def facts_numbers(summary: dict) -> set[str]:
    nums = {
        summary["attempts"], summary["passed"], summary["failed"], summary["active_days"],
        summary["closed_total"], summary["open_total"], summary["closed_in_week"], summary["added"],
        len(summary["needs_retag_ids"]), 7, 20,
    }
    nums |= set(summary["by_cause_failed"].values())
    if summary["closure_rate"] is not None:
        nums.add(round(summary["closure_rate"] * 100))
    out = {str(n) for n in nums}
    # 日期本身允许出现
    for d in (summary["week_start"], summary["week_end"]):
        out |= set(re.findall(r"\d+", d))
        out.add(d)
    return out


def validate_digest(text: str, summary: dict, max_chars: int = 320) -> list[str]:
    v: list[str] = []
    if not text.strip():
        return ["输出为空"]
    allowed = facts_numbers(summary)
    cleaned = text
    for d in (summary["week_start"], summary["week_end"]):
        cleaned = cleaned.replace(d, " ")
    cleaned = re.sub(r"(?m)^\s*\d+[.、．)]\s*", "", cleaned)  # 行首的列表编号不算数字
    for n in set(re.findall(r"\d+", cleaned)):
        if n not in allowed:
            v.append(f"数字 {n} 不在事实里")
    hit = [w for w in JUDGEMENT_WORDS if w in text]
    if hit:
        v.append(f"含评价词：{hit}")
    if len(text) > max_chars:
        v.append(f"超过 {max_chars} 字")
    return v


def _digest_prompt(deterministic_text: str) -> list[dict]:
    system = (
        "你把一份给家长的错题周报改写得更口语、更温和，像老师微信里发的话。规则："
        "不得新增、删除或改动任何数字；不得出现任何题目内容；不得用评价词（粗心、落后、差、笨等）；"
        "保留“您这周能帮的一件事”；总长不超过 300 字。只输出 JSON：{\"text\": 改写后的全文}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": deterministic_text}]


def phrase_parent_digest(deterministic_text: str, summary: dict, call_fn: CallFn | None = None) -> tuple[str, str, list[str]]:
    """返回 (text, source, violations)。任何失败都回退到确定性文本。"""
    fn = call_fn or (http_call if api_configured() else None)
    if fn is None:
        return deterministic_text, "deterministic", []
    messages = _digest_prompt(deterministic_text)
    all_v: list[str] = []
    for _ in range(2):
        try:
            raw = _parse_json(fn(messages))
            text = raw.get("text")
            if not isinstance(text, str):
                raise ContractViolation("缺 text 字段")
        except ContractViolation as e:
            all_v.append(str(e))
            continue
        v = validate_digest(text, summary)
        if not v:
            return text.strip() + "\n", "ai", all_v
        all_v.extend(v)
        messages = messages + [
            {"role": "assistant", "content": json.dumps({"text": text}, ensure_ascii=False)},
            {"role": "user", "content": "上一条输出违反合同：" + "；".join(v) + "。只修正这些问题，重新输出 JSON。"},
        ]
    return deterministic_text, "deterministic", all_v


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
