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
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import __version__
from .ai import api_configured, phrase_parent_digest, sha, suggest_cause
from .ladder import describe_stage
from .model import SOURCES
from .report import parent_digest, render_student_report, weekly_summary
from .service import Service, ServiceError
from .store import StoreError, exists
from .taxonomy import TaxonomyError

STATIC = Path(__file__).resolve().parent.parent / "static"


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


class SuggestBody(BaseModel):
    problem: str
    note: str
    for_id: str | None = None


def create_app(path: Path | str | None = None, today_fn: Callable[[], date] = date.today) -> FastAPI:
    data_path = Path(path or os.environ.get("REDO_RIGHT_FILE", "redo_right_data.json"))
    app = FastAPI(title="再做对", version=__version__)

    def svc() -> Service:
        return Service(data_path, today_fn=today_fn)

    @app.exception_handler(ServiceError)
    @app.exception_handler(StoreError)
    @app.exception_handler(TaxonomyError)
    async def _bad_request(_: Request, exc: Exception):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    def m_json(s: Service, m, ladder) -> dict:
        d = m.to_dict()
        c = s.taxonomy.get(m.cause)
        d.update(
            cause_name=c.name, next_step=c.next_step, redo_mode=c.redo_mode,
            stage_text=describe_stage(m.stage, ladder),
        )
        return d

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    def state():
        s = svc()
        today = today_fn()
        causes = [c.__dict__ for c in s.taxonomy.causes]
        if not exists(data_path):
            return {"initialized": False, "today": today.isoformat(), "causes": causes, "sources": SOURCES}
        doc = s.document()
        ladder = doc["ladder"]
        return {
            "initialized": True,
            "today": today.isoformat(),
            "student": doc["student"],
            "ladder": ladder,
            "causes": causes,
            "sources": SOURCES,
            "retag": [m_json(s, m, ladder) for m in s.pending_retag()],
            "due": [m_json(s, m, ladder) for m in s.due()],
            "week": weekly_summary(doc, today),
            "ai_enabled": api_configured(),
            "version": __version__,
        }

    @app.post("/api/init")
    def init(body: InitBody):
        svc().init(body.student, body.grade, body.ladder)
        return {"ok": True}

    @app.post("/api/mistakes")
    def add(body: AddBody):
        s = svc()
        m = s.add(body.topic, body.cause, body.problem, body.source, body.note)
        accepted = s.resolve_suggestion(body.suggestion, body.cause) if body.suggestion else None
        return {"mistake": m_json(s, m, s.document()["ladder"]), "accepted": accepted}

    @app.post("/api/mistakes/{mid}/redo")
    def redo(mid: str, body: RedoBody):
        s = svc()
        m = s.redo(mid, body.passed, body.seconds, body.note)
        return {"mistake": m_json(s, m, s.document()["ladder"])}

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
        return {"mistake": m_json(s, m, s.document()["ladder"]), "accepted": accepted}

    @app.post("/api/suggest")
    def suggest(body: SuggestBody):
        s = svc()
        sg = suggest_cause(body.problem, body.note, s.taxonomy)
        sid = s.log_suggestion("cause", sha(body.problem + "\n" + body.note), sg.to_dict(), sg.source, body.for_id)
        out = sg.to_dict()
        out["id"] = sid
        out["cause_name"] = s.taxonomy.get(sg.code).name if sg.code else None
        return out

    @app.get("/api/report/weekly", response_class=PlainTextResponse)
    def weekly(week_end: str | None = None):
        s = svc()
        doc = s.document()
        end = date.fromisoformat(week_end) if week_end else today_fn()
        return render_student_report(weekly_summary(doc, end), s.taxonomy, doc)

    @app.get("/api/report/parent", response_class=PlainTextResponse)
    def parent(week_end: str | None = None, ai: int = 0):
        s = svc()
        doc = s.document()
        end = date.fromisoformat(week_end) if week_end else today_fn()
        summary = weekly_summary(doc, end)
        text = parent_digest(summary, s.taxonomy)
        if ai:
            text, source, violations = phrase_parent_digest(text, summary)
            s.log_suggestion("digest", sha(text), {"source": source, "violations": violations}, source)
        return text

    @app.get("/api/ai-stats")
    def ai_stats():
        return svc().ai_stats()

    return app


app = create_app()
