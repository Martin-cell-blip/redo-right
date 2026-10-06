"""本地网页：FastAPI 包一层 Service，页面是一个静态 HTML。

可选依赖：没装 fastapi 时 CLI 照常工作，只是没有网页。
数据文件：环境变量 REDO_RIGHT_FILE，默认当前目录 redo_right_data.json。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import __version__
from .ai import api_configured, phrase_parent_digest, sha, suggest_cause
from .ladder import describe_stage
from .model import SOURCES, Mistake
from .report import parent_digest, render_student_report, weekly_summary
from .service import Service, ServiceError
from .sheets import render_student_sheet, wrap_html
from .store import StoreError, exists
from .taxonomy import Taxonomy, TaxonomyError, load_taxonomy

STATIC = Path(__file__).resolve().parent.parent / "static"
NO_STORE = {"Cache-Control": "no-store"}


class InitBody(BaseModel):
    student: str
    grade: str
    ladder: list[int] | None = None


class AddBody(BaseModel):
    topic: str
    cause: str
    problem: str
    source: str = "作业"
    note: str = ""
    suggestion: str | None = None


class RedoBody(BaseModel):
    passed: bool
    seconds: int | None = None
    note: str = ""


class RetagBody(BaseModel):
    cause: str
    reason: str
    suggestion: str | None = None


class ArchiveBody(BaseModel):
    reason: str


class SuggestBody(BaseModel):
    problem: str
    note: str
    for_id: str | None = None


def create_app(
    path: Path | str | None = None,
    today_fn: Callable[[], date] = date.today,
    taxonomy: Taxonomy | None = None,
) -> FastAPI:
    data_path = Path(path or os.environ.get("REDO_RIGHT_FILE", "redo_right_data.json"))
    tax = taxonomy or load_taxonomy()  # 启动时读一次，不在每个请求里读
    app = FastAPI(title="再做对", version=__version__)

    def svc() -> Service:
        return Service(data_path, taxonomy=tax, today_fn=today_fn)

    @app.exception_handler(ServiceError)
    @app.exception_handler(StoreError)
    @app.exception_handler(TaxonomyError)
    async def _bad_request(_: Request, exc: Exception):
        return JSONResponse(status_code=400, content={"error": str(exc)}, headers=NO_STORE)

    def m_json(m: Mistake, ladder: list[int]) -> dict:
        d = m.to_dict()
        c = tax.get(m.cause)
        d.update(
            cause_name=c.name, next_step=c.next_step, redo_mode=c.redo_mode,
            stage_text=describe_stage(m.stage, ladder), ladder_len=len(ladder),
        )
        return d

    @app.api_route("/", methods=["GET", "HEAD"])
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/state")
    def state():
        today = today_fn()
        base = {
            "today": today.isoformat(),
            "causes": [c.__dict__ for c in tax.causes],
            "sources": list(SOURCES),
            "ai_enabled": api_configured(),
            "version": __version__,
        }
        if not exists(data_path):
            return JSONResponse({**base, "initialized": False}, headers=NO_STORE)
        snap = svc().snapshot(today)
        doc = snap["doc"]
        ladder = doc["ladder"]
        return JSONResponse(
            {
                **base,
                "initialized": True,
                "student": doc["student"],
                "ladder": ladder,
                "retag": [m_json(m, ladder) for m in snap["retag"]],
                "due": [m_json(m, ladder) for m in snap["due"]],
                "open": [m_json(m, ladder) for m in snap["open"]],
                "week": weekly_summary(doc, today),
            },
            headers=NO_STORE,
        )

    @app.post("/api/init")
    def init(body: InitBody):
        if not body.student.strip() or not body.grade.strip():
            raise ServiceError("姓名和年级都要填")
        svc().init(body.student, body.grade, body.ladder)
        return {"ok": True}

    @app.post("/api/mistakes")
    def add(body: AddBody):
        s = svc()
        m = s.add(body.topic, body.cause, body.problem, body.source, body.note)
        accepted = s.resolve_suggestion(body.suggestion, body.cause) if body.suggestion else None
        return {"mistake": m_json(m, s.document()["ladder"]), "accepted": accepted}

    @app.post("/api/mistakes/{mid}/redo")
    def redo(mid: str, body: RedoBody):
        s = svc()
        m = s.redo(mid, body.passed, body.seconds, body.note)
        return {"mistake": m_json(m, s.document()["ladder"])}

    @app.post("/api/mistakes/{mid}/retag")
    def retag(mid: str, body: RetagBody):
        s = svc()
        by = "student"
        accepted = None
        if body.suggestion:
            accepted = s.resolve_suggestion(body.suggestion, body.cause)
            if accepted:
                by = "ai-suggested-accepted"
        m = s.retag(mid, body.cause, body.reason, by=by)
        return {"mistake": m_json(m, s.document()["ladder"]), "accepted": accepted}

    @app.post("/api/mistakes/{mid}/archive")
    def archive(mid: str, body: ArchiveBody):
        s = svc()
        m = s.archive(mid, body.reason)
        return {"mistake": m_json(m, s.document()["ladder"])}

    @app.post("/api/suggest")
    def suggest(body: SuggestBody):
        s = svc()
        sg = suggest_cause(body.problem, body.note, tax)
        sid = s.log_suggestion("cause", sha(body.problem + "\n" + body.note), sg.to_dict(), sg.source, body.for_id)
        out = sg.to_dict()
        out["id"] = sid
        out["cause_name"] = tax.get(sg.code).name if sg.code else None
        return out

    def _end(week_end: str | None) -> date:
        try:
            return date.fromisoformat(week_end) if week_end else today_fn()
        except ValueError as e:
            raise ServiceError("日期格式 YYYY-MM-DD") from e

    @app.get("/api/report/weekly", response_class=PlainTextResponse)
    def weekly(week_end: str | None = None):
        doc = svc().document()
        return render_student_report(weekly_summary(doc, _end(week_end)), tax, doc)

    @app.get("/api/report/parent", response_class=PlainTextResponse)
    def parent(week_end: str | None = None, ai: int = 0):
        s = svc()
        doc = s.document()
        summary = weekly_summary(doc, _end(week_end))
        text = parent_digest(summary, tax)
        if ai:
            text, source, violations = phrase_parent_digest(text, summary)
            s.log_suggestion("digest", sha(text), {"source": source, "violations": violations}, source)
        return text

    @app.get("/api/sheet", response_class=HTMLResponse)
    def sheet(on: str | None = None):
        doc = svc().document()
        day = _end(on)
        return HTMLResponse(
            wrap_html(render_student_sheet(doc, day, tax), f"{doc['student']['name']} 重练单 {day.isoformat()}"),
            headers=NO_STORE,
        )

    @app.get("/api/ai-stats")
    def ai_stats():
        return svc().ai_stats()

    return app


app = create_app()
