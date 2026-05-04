"""
pipedbg.webui
=============
Phase 3 local web UI server.

Provides:
- DAG visualization API payload
- Breakpoint toggling
- Step timeline
- Session sharing by URL
- Simple plan gating for AI explain calls
"""

from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import ai
from .parser import Workflow, parse_pipeline
from .runner import JobStatus, StepStatus, load_secrets, run_job


@dataclass
class UISession:
    session_id: str
    workflow_path: Path
    repo_path: Path
    workflow: Workflow
    breakpoints: set[str] = field(default_factory=set)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    ai_calls: int = 0
    plan_tier: str = "free"


def _workflow_payload(workflow: Workflow) -> dict[str, Any]:
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


def run_ui_server(
    workflow_file: Path,
    repo_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    workflow = parse_pipeline(workflow_file)
    plan_tier = "team" if (Path(repo_path) / ".pipedbg-team").exists() else "free"

    initial_session = UISession(
        session_id=uuid.uuid4().hex[:12],
        workflow_path=workflow_file,
        repo_path=repo_path,
        workflow=workflow,
        plan_tier=plan_tier,
    )

    sessions: dict[str, UISession] = {initial_session.session_id: initial_session}
    web_dir = Path(__file__).parent / "web"

    def get_or_create_session(session_id: str | None) -> UISession:
        if session_id and session_id in sessions:
            return sessions[session_id]
        new_s = UISession(
            session_id=uuid.uuid4().hex[:12],
            workflow_path=workflow_file,
            repo_path=repo_path,
            workflow=parse_pipeline(workflow_file),
            plan_tier=plan_tier,
        )
        sessions[new_s.session_id] = new_s
        return new_s

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            try:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._error_response(500, str(e))

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw) if raw else {}
            except (ValueError, json.JSONDecodeError) as e:
                raise ValueError(f"Invalid JSON: {e}")
            except Exception as e:
                raise RuntimeError(f"Failed to read request: {e}")

        def _serve_static(self, filename: str, content_type: str) -> None:
            try:
                # Security: prevent path traversal attacks
                if ".." in filename or filename.startswith("/"):
                    self.send_error(400, "Invalid path")
                    return
                
                path = web_dir / filename
                # Verify path is within web_dir
                if not path.resolve().is_relative_to(web_dir.resolve()):
                    self.send_error(403, "Access denied")
                    return
                
                if not path.exists():
                    self.send_error(404)
                    return
                
                content = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self._error_response(500, f"Failed to serve {filename}: {e}")

        def _error_response(self, status: int, message: str) -> None:
            try:
                error_payload = {"error": message, "status": status}
                self._json(status, error_payload)
            except Exception:
                self.send_error(status)

        def _session_payload(self, s: UISession) -> dict[str, Any]:
            return {
                "session_id": s.session_id,
                "share_url": f"http://{host}:{port}/?session={s.session_id}",
                "workflow": _workflow_payload(s.workflow),
                "breakpoints": sorted(s.breakpoints),
                "timeline": s.timeline,
                "plan": {
                    "tier": s.plan_tier,
                    "ai_limit": None if s.plan_tier == "team" else 5,
                    "ai_calls_used": s.ai_calls,
                },
            }

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                if parsed.path in ("/", "/index.html"):
                    return self._serve_static("index.html", "text/html; charset=utf-8")
                if parsed.path == "/styles.css":
                    return self._serve_static("styles.css", "text/css; charset=utf-8")
                if parsed.path == "/app.js":
                    return self._serve_static("app.js", "application/javascript; charset=utf-8")
                if parsed.path == "/api/state":
                    query = parse_qs(parsed.query)
                    session_id = query.get("session", [None])[0]
                    session = get_or_create_session(session_id)
                    return self._json(200, self._session_payload(session))
                self.send_error(404)
            except Exception as e:
                self._error_response(500, f"GET request failed: {e}")

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                
                try:
                    payload = self._read_json()
                except ValueError as e:
                    return self._error_response(400, str(e))
                
                session = get_or_create_session(payload.get("session_id"))

                if parsed.path == "/api/breakpoint":
                    try:
                        step_id = payload.get("step_id")
                        enabled = bool(payload.get("enabled", True))
                        if not step_id:
                            return self._error_response(400, "step_id is required")
                        if enabled:
                            session.breakpoints.add(step_id)
                        else:
                            session.breakpoints.discard(step_id)
                        return self._json(200, self._session_payload(session))
                    except Exception as e:
                        return self._error_response(500, f"Breakpoint toggle failed: {e}")

                if parsed.path == "/api/run":
                    try:
                        no_docker = bool(payload.get("no_docker", True))
                        dry_run = bool(payload.get("dry_run", False))
                        env_file = session.repo_path / ".env"
                        secrets = load_secrets(env_file)

                        session.timeline.append(
                            {
                                "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                "job": "system",
                                "step": "run-start",
                                "status": "INFO",
                            }
                        )

                        for job_id in session.workflow.execution_order():
                            try:
                                job = session.workflow.jobs[job_id]
                                result = run_job(
                                    job=job,
                                    workflow=session.workflow,
                                    repo_path=session.repo_path,
                                    secrets=secrets,
                                    dry_run=dry_run,
                                    force_breakpoints=list(session.breakpoints),
                                    use_docker=not no_docker,
                                )

                                for step_result in result.step_results:
                                    session.timeline.append(
                                        {
                                            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                            "job": job.name,
                                            "step": step_result.step.display_name(),
                                            "status": step_result.status.name,
                                            "exit_code": step_result.exit_code,
                                            "duration": round(step_result.duration_seconds, 3),
                                        }
                                    )

                                if result.status == JobStatus.FAILED:
                                    break
                            except Exception as e:
                                session.timeline.append(
                                    {
                                        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                        "job": job_id,
                                        "step": "error",
                                        "status": "ERROR",
                                        "error": str(e),
                                    }
                                )
                                break

                        return self._json(200, self._session_payload(session))
                    except Exception as e:
                        return self._error_response(500, f"Pipeline execution failed: {e}")

                if parsed.path == "/api/explain":
                    try:
                        if session.plan_tier != "team" and session.ai_calls >= 5:
                            return self._error_response(
                                402,
                                "AI explain limit reached for free tier. Create `.pipedbg-team` in repo root to unlock team mode.",
                            )
                        session.ai_calls += 1
                        dummy = payload.get("failed_step", {})
                        explanation = {
                            "explanation": "No failed step context provided.",
                            "suggestion": "",
                        }
                        if dummy:
                            class _Step:
                                def __init__(self, name: str, sid: str):
                                    self._name = name
                                    self.id = sid

                                def display_name(self) -> str:
                                    return self._name

                            class _Result:
                                def __init__(self, name: str, sid: str, logs: list[str], exit_code: int):
                                    self.step = _Step(name, sid)
                                    self.logs = logs
                                    self.exit_code = exit_code

                            fake = _Result(
                                dummy.get("name", "failed-step"),
                                dummy.get("id", "failed-step"),
                                dummy.get("logs", []),
                                int(dummy.get("exit_code", 1)),
                            )
                            explanation = ai.explain_failure(fake)
                        return self._json(200, {"ai": explanation, "ai_calls": session.ai_calls})
                    except Exception as e:
                        return self._error_response(500, f"AI explain failed: {e}")

                self.send_error(404)
            except Exception as e:
                self._error_response(500, f"POST request failed: {e}")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/?session={initial_session.session_id}"
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    print(f"pipedbg UI running on {url}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()
