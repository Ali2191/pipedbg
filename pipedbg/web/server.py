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
                
                # Check if the user has permission to access the session
                if session.session_id != req.session_id:
                    raise Exception("Unauthorized access to session")

            run_job(job, secrets, event_sink, req.dry_run)
            job_state.status = "completed"
            job_state.ended_at = _timestamp()
            _emit_event(loop, {"type": "job_end", "job_id": job_id, "job_name": job.name, "status": job_state.status})
        session.status = "completed"
        session.ended_at = _timestamp()
        _emit_event(loop, {"type": "pipeline_end", "workflow": workflow.name, "status": session.status})
    except Exception as e:
        session.status = "failed"
        session.ended_at = _timestamp()
        _emit_event(loop, {"type": "pipeline_end", "workflow": workflow.name, "status": session.status, "error": str(e)})
        raise