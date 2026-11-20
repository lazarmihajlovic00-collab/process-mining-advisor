"""FastAPI app: REST API + the dashboard (web/)."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, discovery, llm
from .advisor import runner
from .levers import LEVERS
from .scenario import ISSUE_LABELS_SR, ISSUES, build
from .service import ServiceError, Workspace
from .store import Store

WEB = Path(__file__).resolve().parent.parent / "web"


class DemoReq(BaseModel):
    seed: int = 7
    issues: list[str] | None = None


class WhatIfReq(BaseModel):
    levers: list[str]


class DecisionReq(BaseModel):
    accept: bool
    note: str = ""


class ProposeReq(BaseModel):
    lever: str
    priority: int = 2
    rationale: str = ""


class AdviseReq(BaseModel):
    question: str | None = None


def create_app(ws: Workspace | None = None) -> FastAPI:
    ws = ws or Workspace(Store(os.environ.get("PMA_DB", "data/advisor.db")))
    if ws.current is None:
        ws.load_scenario()
    app = FastAPI(title="Process-mining automation advisor")
    app.state.ws = ws

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ServiceError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/status")
    def status():
        run = ws.run()
        return {"run": run.id, "label": run.label, "source": run.source, "simulated": run.scenario is not None,
                "llm_provider": llm.provider(), "llm_model": llm.model_name(), "runs": ws.store.runs(),
                "issues": [{"id": i, "label": ISSUE_LABELS_SR[i]} for i in ISSUES],
                "planted": list(run.scenario.issues) if run.scenario else None}

    @app.post("/api/runs/demo")
    def new_demo(req: DemoReq):
        if req.issues is None:
            run = ws.load_scenario()
        else:
            bad = [i for i in req.issues if i not in ISSUES]
            if bad:
                raise HTTPException(400, f"unknown issues {bad}")
            run = ws.load_scenario(build(req.seed, req.issues), f"Scenario {req.seed} ({len(req.issues)} problema)")
        return {"run": run.id, "label": run.label}

    @app.post("/api/runs/upload")
    async def upload(file: UploadFile = File(...)):
        suffix = Path(file.filename or "log.csv").suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tempfile.gettempdir()) as tmp:
            tmp.write(await file.read())
        run = guard(ws.load_file, tmp.name, None, file.filename)
        return {"run": run.id, "label": run.label, "log_check": run._cache.get("log_check")}

    @app.get("/api/overview")
    def overview():
        return {**guard(ws.overview), "model": ws.model()}

    @app.get("/api/dfg")
    def dfg(min_share: float = 0.0):
        return analysis.dfg(ws.run().an, min_share)

    @app.get("/api/variants")
    def variants(top: int = 12):
        return analysis.variants(ws.run().an, top)

    @app.get("/api/activities")
    def activities():
        an = ws.run().an
        return {"activities": analysis.activity_stats(an), "candidates": analysis.automation_candidates(an),
                "waiting": analysis.waiting_hotspots(an), "rework": analysis.rework(an),
                "resources": analysis.resources(an)}

    @app.get("/api/findings")
    def findings_():
        return ws.findings()

    @app.get("/api/estimates")
    def estimates():
        return ws.estimates()

    @app.get("/api/levers")
    def levers_():
        return [{"id": l.id, "title": l.title, "kind": l.kind, "description": l.description, "fixes": l.fixes}
                for l in LEVERS.values()]

    @app.get("/api/compare")
    def compare():
        return ws.compare()

    @app.post("/api/whatif")
    def whatif(req: WhatIfReq):
        return {"simulation": guard(ws.whatif, req.levers), "package": guard(ws.package, req.levers)}

    @app.get("/api/conformance")
    def conformance():
        return ws.conformance()

    @app.get("/api/recommendations")
    def recommendations():
        return ws.recommendations()

    @app.post("/api/recommendations/{rec_id}/decision")
    def decide(rec_id: int, req: DecisionReq, x_user: str = Header(default="")):
        return guard(ws.decide, rec_id, req.accept, x_user, req.note)

    @app.post("/api/recommendations")
    def propose(req: ProposeReq, x_user: str = Header(default="")):
        if not x_user:
            raise HTTPException(400, "X-User header required")
        return guard(ws.propose, req.lever, req.priority, req.rationale or "Ručno dodata preporuka.", "person", x_user)

    @app.post("/api/advise")
    def advise(req: AdviseReq, x_user: str = Header(default="analiticar")):
        res = runner.advise(ws, x_user or "analiticar", req.question)
        return res.to_dict()

    @app.get("/api/report")
    def report():
        return ws.store.latest_report(ws.run().id) or {}

    @app.get("/api/audit")
    def audit():
        return {"verify": ws.store.verify_audit(), "entries": ws.store.audit_log(100)}

    @app.get("/api/export/log.csv")
    def export_log():
        buf = io.StringIO()
        cols = [c for c in ws.run().an.df.columns if c not in ("prev_activity", "seq")]
        ws.run().an.df[cols].to_csv(buf, index=False)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=event_log.csv"})

    @app.get("/api/export/model.bpmn")
    def export_bpmn():
        with tempfile.TemporaryDirectory() as d:
            p = discovery.bpmn_xml(ws.run().an, Path(d) / "model.bpmn")
            data = p.read_bytes()
        return Response(data, media_type="application/xml",
                        headers={"Content-Disposition": "attachment; filename=discovered_model.bpmn"})

    if WEB.exists():
        @app.get("/")
        def index():
            return FileResponse(WEB / "index.html")

        app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
