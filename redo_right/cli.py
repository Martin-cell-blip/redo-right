"""命令行入口：python -m redo_right <命令>。"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .ladder import describe_stage
from .model import SOURCES
from .classroom import heatmap, load_class, render_heatmap_md, suggestions
from .report import export_csv, parent_digest, render_student_report, weekly_summary
from .service import Service, ServiceError
from .sheets import bill_of_materials, render_bom_md, render_student_sheet, wrap_html
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

    s = sub.add_parser("retag", help="重新归因（累计两次做错后必须先做这一步）")
    s.add_argument("id")
    s.add_argument("--cause", required=True)
    s.add_argument("--reason", required=True, help="这次想清楚了错在哪？")
    s.add_argument("--by", default="student", choices=("student", "teacher"))

    s = sub.add_parser("attended", help="老师已处理，清除关注标记")
    s.add_argument("id")
    s.add_argument("--note", required=True)

    s = sub.add_parser("report", help="学生周报（Markdown）")
    s.add_argument("--week-end", type=_parse_date, default=None, help="周报截止日，默认今天")
    s.add_argument("--out", default=None, help="写入文件；不填则打印")

    s = sub.add_parser("export", help="导出 CSV（教研/家长可用 Excel 打开）")
    s.add_argument("--out", required=True)

    s = sub.add_parser("parent", help="家长周报（纯文字，三个数字一件事）")
    s.add_argument("--week-end", type=_parse_date, default=None)
    s.add_argument("--out", default=None)

    s = sub.add_parser("sheet", help="本学生的重练单（A4 HTML，可直接打印）")
    s.add_argument("--on", type=_parse_date, default=None, help="打印日期，默认今天")
    s.add_argument("--out", required=True)

    s = sub.add_parser("class", help="班级视图（一个学生一份 JSON，放在同一文件夹）")
    s.add_argument("--dir", required=True, help="学生数据文件夹")
    cs = s.add_subparsers(dest="class_cmd", required=True)
    c1 = cs.add_parser("heatmap", help="知识点 × 错因热力图 + 教研建议（Markdown）")
    c1.add_argument("--out", default=None)
    c2 = cs.add_parser("sheets", help="全班重练单 + 物料清单")
    c2.add_argument("--on", type=_parse_date, default=None)
    c2.add_argument("--out-dir", required=True)

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
        if args.cmd == "class":
            tax = load_taxonomy(args.taxonomy) if args.taxonomy else load_taxonomy()
            docs = load_class(args.dir)
            if args.class_cmd == "heatmap":
                heat = heatmap(docs)
                text = render_heatmap_md(heat, suggestions(heat, tax), tax)
                if args.out:
                    Path(args.out).write_text(text, encoding="utf-8")
                    print(f"热力图已写入 {args.out}")
                else:
                    print(text, end="")
                return 0
            on = args.on or (args.today or date.today())
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            bom = bill_of_materials(docs, on)
            printed = 0
            for doc in docs:
                if doc["student"]["name"] in bom["skipped"]:
                    continue
                body = render_student_sheet(doc, on, tax)
                (out_dir / f"重练单_{doc['student']['name']}_{on.isoformat()}.html").write_text(
                    wrap_html(body, f"{doc['student']['name']} 重练单"), encoding="utf-8"
                )
                printed += 1
            (out_dir / f"物料清单_{on.isoformat()}.md").write_text(render_bom_md(bom), encoding="utf-8")
            print(f"已生成 {printed} 份重练单，共 {bom['total_pages']} 页，物料清单见 {out_dir}")
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
            retags = svc.pending_retag()
            if retags:
                print("先重新归因（累计两次做错，想清楚错在哪一类再重做）：")
                for m in retags:
                    print("  " + _fmt(m, ladder))
            items = svc.due()
            if not items and not retags:
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
        if args.cmd == "retag":
            m = svc.retag(args.id, args.cause, args.reason, by=args.by)
            c = svc.taxonomy.get(m.cause)
            print(f"已重新归因为 {c.code} {c.name}。下次重做 {m.due}，重做前先：{c.next_step}")
            return 0
        if args.cmd == "attended":
            m = svc.clear_attention(args.id, args.note)
            print(f"已清除 {m.id} 的关注标记")
            return 0
        if args.cmd == "report":
            doc = svc.document()
            week_end = args.week_end or (args.today or date.today())
            text = render_student_report(weekly_summary(doc, week_end), svc.taxonomy, doc)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
                print(f"周报已写入 {args.out}")
            else:
                print(text, end="")
            return 0
        if args.cmd == "export":
            Path(args.out).write_text(export_csv(svc.document(), svc.taxonomy), encoding="utf-8-sig")
            print(f"已导出 {args.out}")
            return 0
        if args.cmd == "parent":
            week_end = args.week_end or (args.today or date.today())
            text = parent_digest(weekly_summary(svc.document(), week_end), svc.taxonomy)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
                print(f"家长周报已写入 {args.out}")
            else:
                print(text, end="")
            return 0
        if args.cmd == "sheet":
            on = args.on or (args.today or date.today())
            doc = svc.document()
            Path(args.out).write_text(
                wrap_html(render_student_sheet(doc, on, svc.taxonomy), f"{doc['student']['name']} 重练单"),
                encoding="utf-8",
            )
            print(f"重练单已写入 {args.out}")
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
