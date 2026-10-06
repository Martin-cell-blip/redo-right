"""命令行入口：python -m redo_right <命令>。"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .ladder import describe_stage
from .model import SOURCES
from .service import Service, ServiceError
from .store import StoreError
from .taxonomy import TaxonomyError, load_taxonomy

DEFAULT_FILE = "redo_right_data.json"


def _parse_ladder(text: str) -> list[int]:
    try:
        return [int(x) for x in text.split(",") if x.strip()]
    except ValueError as e:
        raise argparse.ArgumentTypeError("阶梯格式：逗号分隔的正整数，例如 1,7,21") from e


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError("日期格式 YYYY-MM-DD") from e


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="redo", description="再做对：错题闭环工具")
    p.add_argument("--file", default=DEFAULT_FILE, help=f"数据文件（默认 {DEFAULT_FILE}）")
    p.add_argument("--taxonomy", default=None, help="自定义错因表 JSON（默认内置六类）")
    p.add_argument("--today", type=_parse_date, default=None, help="覆盖今天日期（测试/补录用）")
    p.add_argument("--version", action="version", version=f"redo-right {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="新建学生数据文件")
    s.add_argument("--student", required=True)
    s.add_argument("--grade", required=True, help="例如 高一")
    s.add_argument("--ladder", type=_parse_ladder, default=None, help="重做间隔天数，默认 1,7,21")

    s = sub.add_parser("add", help="记一道错题")
    s.add_argument("--topic", required=True, help="知识点，例如 数列-递推")
    s.add_argument("--cause", required=True, help="错因编码，见 causes 命令")
    s.add_argument("--problem", required=True, help="题目引用（题号/开头），不要写答案")
    s.add_argument("--source", default="作业", choices=SOURCES)
    s.add_argument("--note", default="", help="当时怎么想的（归因依据）")

    sub.add_parser("due", help="今天到期要重做的题")

    s = sub.add_parser("redo", help="记录一次重做结果")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--pass", dest="passed", action="store_true", help="做对了")
    g.add_argument("--fail", dest="failed", action="store_true", help="又错了")
    s.add_argument("--seconds", type=int, default=None, help="用时（秒）")
    s.add_argument("--note", default="")

    s = sub.add_parser("archive", help="放弃这道题（须写原因）")
    s.add_argument("id")
    s.add_argument("--reason", required=True)

    s = sub.add_parser("list", help="列出错题")
    s.add_argument("--status", choices=("open", "closed", "archived"), default=None)

    sub.add_parser("causes", help="显示错因表")
    return p


def _svc(args) -> Service:
    tax = load_taxonomy(args.taxonomy) if args.taxonomy else None
    today_fn = (lambda d=args.today: d) if args.today else date.today
    return Service(Path(args.file), taxonomy=tax, today_fn=today_fn)


def _fmt(m, ladder) -> str:
    due = m.due or "—"
    return f"{m.id}  [{m.status}] {m.topic} | {m.cause} | {describe_stage(m.stage, ladder)} | 错{m.fail_count}次 | 下次 {due} | {m.problem[:30]}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "causes":
            tax = load_taxonomy(args.taxonomy) if args.taxonomy else load_taxonomy()
            for c in tax.causes:
                print(f"{c.code}  {c.name}  —— {c.description}\n     重做前：{c.next_step}")
            return 0
        svc = _svc(args)
        if args.cmd == "init":
            doc = svc.init(args.student, args.grade, args.ladder)
            print(f"已创建 {svc.path}（学生 {doc['student']['name']}，阶梯 {doc['ladder']}）")
            return 0
        if args.cmd == "add":
            m = svc.add(args.topic, args.cause, args.problem, args.source, args.note)
            cause = svc.taxonomy.get(m.cause)
            print(f"已记录 {m.id}，下次重做 {m.due}。重做前先：{cause.next_step}")
            return 0
        ladder = svc.document()["ladder"]
        if args.cmd == "due":
            items = svc.due()
            if not items:
                print("今天没有到期的错题。")
            for m in items:
                c = svc.taxonomy.get(m.cause)
                print(_fmt(m, ladder))
                print(f"     重做前先：{c.next_step}")
            return 0
        if args.cmd == "redo":
            m = svc.redo(args.id, passed=bool(args.passed), seconds=args.seconds, note=args.note)
            if m.status == "closed":
                print(f"🎉 {m.id} 闭环：三个间隔点都做对了。")
            elif args.passed:
                print(f"做对了。{describe_stage(m.stage, ladder)}，下次 {m.due}")
            else:
                print(f"又错了（累计 {m.fail_count} 次），回到阶梯底部，下次 {m.due}")
            return 0
        if args.cmd == "archive":
            m = svc.archive(args.id, args.reason)
            print(f"已归档 {m.id}")
            return 0
        if args.cmd == "list":
            for m in svc.list(args.status):
                print(_fmt(m, ladder))
            return 0
        parser.error("未知命令")
        return 2
    except (ServiceError, StoreError, TaxonomyError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
