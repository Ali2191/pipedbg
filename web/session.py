"""
Session state management for the web UI.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..parsers.base import Workflow


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class StepState:
    id: str
    name: str
    status: str = "pending"
    logs: list[str] = field(default_factory=list)
    duration: float = 0.0
    exit_code: int | None = None
    breakpoint_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "duration": self.duration,
            "exit_code": self.exit_code,
            "breakpoint_active": self.breakpoint_active,
        }


@dataclass
class JobState:
    id: str
    name: str
    status: str = "pending"
    steps: list[StepState] = field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass
class PipelineSession:
    session_id: str
    workflow_path: Path
    workflow: Workflow | None = None
    jobs: dict[str, JobState] = field(default_factory=dict)
    status: str = "idle"
    started_at: str | None = None
    ended_at: str | None = None
    breakpoints: set[str] = field(default_factory=set)
    current_breakpoint: dict[str, Any] | None = None
    running: bool = False
    share_url: str | None = None
    viewer: bool = False

    cancel_event: threading.Event = field(default_factory=threading.Event)
    breakpoint_controller: "BreakpointController" = field(default_factory=lambda: BreakpointController())

    def reset(self, workflow: Workflow) -> None:
        self.workflow = workflow
        self.jobs = {}
        for job_id, job in workflow.jobs.items():
            steps = [StepState(id=s.id, name=s.display_name()) for s in job.steps]
            self.jobs[job_id] = JobState(id=job_id, name=job.name, steps=steps)
        self.status = "idle"
        self.started_at = None
        self.ended_at = None
        self.current_breakpoint = None
        self.running = False
        self.cancel_event.clear()
        self.breakpoint_controller.reset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workflow_path": str(self.workflow_path),
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "jobs": {job_id: job.to_dict() for job_id, job in self.jobs.items()},
            "breakpoints": sorted(self.breakpoints),
            "current_breakpoint": self.current_breakpoint,
            "share_url": self.share_url,
            "viewer": self.viewer,
        }


class BreakpointController:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._action: str | None = None

    def reset(self) -> None:
        self._event.clear()
        self._action = None

    def wait(self) -> str:
        self._event.wait()
        return self._action or "resume"

    def resume(self) -> None:
        self._action = "resume"
        self._event.set()

    def skip(self) -> None:
        self._action = "skip"
        self._event.set()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, PipelineSession] = {}

    def create(self, workflow_path: Path) -> PipelineSession:
        session_id = uuid4().hex
        session = PipelineSession(session_id=session_id, workflow_path=workflow_path)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> PipelineSession | None:
        return self._sessions.get(session_id)

    def get_or_create(self, workflow_path: Path, session_id: str | None = None) -> PipelineSession:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create(workflow_path)

    def all(self) -> list[PipelineSession]:
        return list(self._sessions.values())
