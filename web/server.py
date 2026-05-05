"""
FastAPI web UI backend for pipedbg.
"""
from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..auth import ProFeatureError, get_license, get_usage_state
from ..auth.gate import require_pro
from ..parsers import parse_any
from ..parser import find_workflows
from ..runner import JobStatus, StepStatus, load_secrets, run_job
from .session import PipelineSession, SessionStore
from .share import get_share_url


class RunRequest(BaseModel):
    session_id: str | None = None
    workflow: str | None = None
    jobs: list[str] | None = None
    dry_run: bool = False
    env_file: str | None = None


app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_sessions = SessionStore()
_executor = ThreadPoolExecutor(max_workers=1)
_event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active.discard(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        if not self.active:
            return
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = ConnectionManager()


def _emit_event(loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
    loop.call_soon_threadsafe(_event_queue.put_nowait, event)


def _workflow_payload(workflow) -> dict[str, Any]:
    jobs_payload = {}
    for job_id, job in workflow.jobs.items():
        jobs_payload[job_id] = {
            "id": job.id,
            "name": job.name,
            "runs_on": job.runs_on,
            "env": job.env,
            "needs": job.needs,
            "steps": [
                {
                    "id": step.id,
                    "name": step.display_name(),
                    "env": step.env,
                    "run": step.run,
                    "breakpoint": step.breakpoint,
                }
                for step in job.steps
            ],
        }
    return {
        "name": workflow.name,
        "platform": workflow.platform,
        "path": str(workflow.path),
        "env": workflow.env,
        "jobs": jobs_payload,
        "levels": workflow.job_levels(),
    }


def _get_default_session() -> PipelineSession:
    session_id = getattr(app.state, "default_session_id", None)
    session = _sessions.get(session_id) if session_id else None
    if not session:
        wf_path = getattr(app.state, "workflow_path", None)
        if not wf_path:
            raise RuntimeError("Workflow path not configured")
        session = _sessions.get_or_create(Path(wf_path), session_id)
        app.state.default_session_id = session.session_id
    return session


def _get_session(session_id: str | None) -> PipelineSession:
    if session_id:
        session = _sessions.get(session_id)
        if session:
            return session
    return _get_default_session()


def _normalize_status(status: str) -> str:
    if status.upper() == "SUCCESS":
        return "passed"
    if status.upper() == "FAILED":
        return "failed"
    if status.upper() == "SKIPPED":
        return "skipped"
    return status.lower()


def _run_pipeline(session: PipelineSession, workflow_path: Path, req: RunRequest, loop: asyncio.AbstractEventLoop) -> None:
    try:
        workflow = parse_any(workflow_path)
        session.reset(workflow)
        session.running = True
        session.status = "running"
        session.started_at = _timestamp()

        _emit_event(loop, {"type": "pipeline_start", "workflow": workflow.name})

        env_file = Path(req.env_file) if req.env_file else (Path(app.state.repo_path) / ".env")
        secrets = load_secrets(env_file)

        job_order = workflow.execution_order()
        if req.jobs:
            job_order = [job_id for job_id in job_order if job_id in req.jobs]

        for job_id in job_order:
            if session.cancel_event.is_set():
                session.status = "canceled"
                break

            job = workflow.jobs[job_id]
            job_state = session.jobs[job_id]
            job_state.status = "running"
            job_state.started_at = job_state.started_at or _timestamp()
            _emit_event(loop, {"type": "job_start", "job_id": job_id, "job_name": job.name})

            def event_sink(event: dict[str, Any]) -> None:
                etype = event.get("type")
                if etype == "step_start":
                    step_index = event.get("step_index")
                    if step_index is not None:
                        step_state = job_state.steps[step_index]
                        step_state.status = "running"
                elif etype == "step_end":
                    step_index = event.get("step_index")
                    if step_index is not None:
                        step_state = job_state.steps[step_index]
                        step_state.status = _normalize_status(event.get("status", "unknown"))
                        step_state.duration = float(event.get("duration", 0.0))
                        step_state.exit_code = event.get("exit_code")
                elif etype == "log":
                    step_index = event.get("step_index")
                    line = event.get("line", "")
                    if step_index is not None:
                        job_state.steps[step_index].logs.append(line)
                elif etype == "breakpoint_hit":
                    step_index = event.get("step_index")
                    if step_index is not None:
                        job_state.steps[step_index].breakpoint_active = True
                        session.current_breakpoint = {
                            "job_id": job_id,
                            "step_index": step_index,
                            "step_name": event.get("step_name"),
                        }
                elif etype == "breakpoint_resume":
                    step_index = event.get("step_index")
                    if step_index is not None:
                        job_state.steps[step_index].breakpoint_active = False
                        session.current_breakpoint = None

                _emit_event(loop, event)

            def breakpoint_handler(job_id: str, step_index: int, step_name: str) -> str:
                event_sink({
                    "type": "breakpoint_hit",
                    "job_id": job_id,
                    "step_index": step_index,
                    "step_name": step_name,
                })
                action = session.breakpoint_controller.wait()
                event_sink({
                    "type": "breakpoint_resume",
                    "job_id": job_id,
                    "step_index": step_index,
                    "step_name": step_name,
                    "action": action,
                })
                return action

            result = run_job(
                job=job,
                workflow=workflow,
                repo_path=Path(app.state.repo_path),
                secrets=secrets,
                dry_run=req.dry_run,
                force_breakpoints=list(session.breakpoints),
                use_docker=True,
                event_sink=event_sink,
                cancel_event=session.cancel_event,
                breakpoint_handler=breakpoint_handler,
            )

            job_state.status = "passed" if result.status == JobStatus.SUCCESS else "failed"
            job_state.ended_at = _timestamp()
            _emit_event(loop, {
                "type": "job_end",
                "job_id": job_id,
                "status": job_state.status,
                "duration": result.duration_seconds,
            })

            if result.status == JobStatus.FAILED:
                session.status = "failed"
                break

        if session.status == "running":
            session.status = "passed"
        session.running = False
        session.ended_at = _timestamp()
        _emit_event(loop, {"type": "pipeline_end", "status": session.status, "duration": 0.0})
    except Exception as e:
        session.status = "failed"
        session.running = False
        _emit_event(loop, {"type": "pipeline_end", "status": "failed", "error": str(e)})


def _timestamp() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


async def _event_broadcaster() -> None:
    while True:
        event = await _event_queue.get()
        await _manager.broadcast(event)


@app.on_event("startup")
async def _on_startup() -> None:
    @app.get("/")
    async def index() -> HTMLResponse:
        index_path = STATIC_DIR / "index.html"
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    app.state.loop = asyncio.get_running_loop()
    asyncio.create_task(_event_broadcaster())


@app.get("/api/session")
async def get_session(session: str | None = None, viewer: str | None = None) -> JSONResponse:
    s = _get_session(session)
    if viewer == "1":
        s.viewer = True
    payload = s.to_dict()
    if s.workflow:
        payload["workflow"] = _workflow_payload(s.workflow)
    return JSONResponse(payload)


@app.get("/api/workflows")
async def get_workflows() -> JSONResponse:
    repo_path = Path(app.state.repo_path)
    paths = find_workflows(repo_path)
    return JSONResponse({"workflows": [str(p) for p in paths]})


@app.get("/api/license")
async def get_license_info() -> JSONResponse:
    lic = get_license()
    usage = get_usage_state()
    return JSONResponse({
        "tier": lic.tier if lic else "free",
        "expires_at": lic.expires_at.isoformat() if lic else None,
        "features": lic.features if lic else [],
        "ai_limit": None if (lic and lic.tier == "pro") else 10,
        "ai_calls_used": usage.ai_calls,
    })


@app.post("/api/run")
async def post_run(req: RunRequest) -> JSONResponse:
    session = _get_session(req.session_id)
    if session.viewer:
        return JSONResponse({"error": "Viewer sessions cannot start runs."}, status_code=403)

    if session.running:
        return JSONResponse({"error": "Pipeline already running."}, status_code=409)

    workflow_path = Path(req.workflow) if req.workflow else session.workflow_path
    session.workflow_path = workflow_path

    loop = app.state.loop
    _executor.submit(_run_pipeline, session, workflow_path, req, loop)
    return JSONResponse({"status": "started", "session_id": session.session_id})


@app.post("/api/cancel")
async def post_cancel(session_id: str | None = None) -> JSONResponse:
    session = _get_session(session_id)
    session.cancel_event.set()
    return JSONResponse({"status": "cancel_requested"})


@app.post("/api/breakpoint/resume")
async def post_breakpoint_resume(session_id: str | None = None) -> JSONResponse:
    session = _get_session(session_id)
    session.breakpoint_controller.resume()
    return JSONResponse({"status": "resumed"})


@app.post("/api/breakpoint/skip")
async def post_breakpoint_skip(session_id: str | None = None) -> JSONResponse:
    session = _get_session(session_id)
    session.breakpoint_controller.skip()
    return JSONResponse({"status": "skipped"})


@app.get("/api/logs/{job_id}/{step_index}")
async def get_logs(job_id: str, step_index: int, session: str | None = None) -> JSONResponse:
    s = _get_session(session)
    job_state = s.jobs.get(job_id)
    if not job_state:
        return JSONResponse({"error": "job not found"}, status_code=404)
    if step_index < 0 or step_index >= len(job_state.steps):
        return JSONResponse({"error": "step not found"}, status_code=404)
    return JSONResponse({"logs": job_state.steps[step_index].logs})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await _manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except Exception:
                payload = {}
            if payload.get("type") == "resume":
                session = _get_session(payload.get("session_id"))
                session.breakpoint_controller.resume()
            elif payload.get("type") == "skip":
                session = _get_session(payload.get("session_id"))
                session.breakpoint_controller.skip()
    except WebSocketDisconnect:
        _manager.disconnect(websocket)


@require_pro("session_sharing")
def _get_public_share_url(port: int) -> str:
    return get_share_url(port)


def run_ui_server(
    workflow_path: Path,
    repo_path: Path,
    host: str = "127.0.0.1",
    port: int = 7337,
    open_browser: bool = True,
    share: bool = False,
) -> None:
    app.state.repo_path = str(repo_path)
    app.state.workflow_path = str(workflow_path)

    session = _sessions.get_or_create(workflow_path)
    session.reset(parse_any(workflow_path))

    if share:
        public_url = _get_public_share_url(port)
        session.share_url = f"{public_url}/?session={session.session_id}&viewer=1"

    url = f"http://{host}:{port}/?session={session.session_id}"
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
