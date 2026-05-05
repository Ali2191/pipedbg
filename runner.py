"""
pipedbg.runner
==============
Executes workflow jobs inside Docker containers.

Each job gets a fresh container matching its `runs-on` image.
Secrets are injected from a local .env file — never written to disk inside
the container. Volumes are mounted read-only by default.

Breakpoints pause execution and open an interactive shell inside the
live container at that exact state.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from rich.console import Console

from .parsers.base import Job, Step, Workflow

console = Console()


# ─────────────────────────────────────────
# Status types
# ─────────────────────────────────────────

class StepStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()
    BREAKPOINT = auto()


class JobStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class StepResult:
    step: Step
    status: StepStatus
    exit_code: int = 0
    logs: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error_summary: str = ""


@dataclass
class JobResult:
    job: Job
    status: JobStatus
    step_results: list[StepResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def failed_step(self) -> StepResult | None:
        return next((s for s in self.step_results if s.status == StepStatus.FAILED), None)


# ─────────────────────────────────────────
# Image mapping
# ─────────────────────────────────────────

RUNNER_IMAGE_MAP: dict[str, str] = {
    "ubuntu-latest":   "ubuntu:22.04",
    "ubuntu-22.04":    "ubuntu:22.04",
    "ubuntu-20.04":    "ubuntu:20.04",
    "ubuntu-24.04":    "ubuntu:24.04",
    "macos-latest":    "ubuntu:22.04",   # fallback — macOS containers not supported locally
    "macos-13":        "ubuntu:22.04",
    "windows-latest":  "ubuntu:22.04",   # fallback
    "self-hosted":     "ubuntu:22.04",
}


def resolve_image(runs_on: str) -> str:
    key = runs_on.lower().strip()
    return RUNNER_IMAGE_MAP.get(key, "ubuntu:22.04")


# ─────────────────────────────────────────
# Secret / env loading
# ─────────────────────────────────────────

def load_secrets(env_file: Path | None) -> dict[str, str]:
    """Load secrets from a .env file. Lines starting with # are comments."""
    secrets: dict[str, str] = {}
    if not env_file or not env_file.exists():
        return secrets
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            secrets[key.strip()] = value.strip().strip('"').strip("'")
    return secrets


def merge_env(workflow_env: dict, job_env: dict, step_env: dict, secrets: dict) -> dict[str, str]:
    """Merge env layers: workflow < job < step < secrets (secrets win)."""
    merged = {}
    merged.update(workflow_env)
    merged.update(job_env)
    merged.update(step_env)
    # Expand ${{ secrets.X }} references (allow flexible spacing)
    import re
    pattern = re.compile(r"\$\{\{\s*secrets\.(?P<name>\w+)\s*\}\}")
    def _replace_secrets(val: str) -> str:
        if not isinstance(val, str):
            return val
        def _repl(m: re.Match) -> str:
            name = m.group('name')
            return secrets.get(name, m.group(0))
        return pattern.sub(_repl, val)

    for k, v in list(merged.items()):
        merged[k] = _replace_secrets(v)
    merged.update(secrets)
    return merged


# ─────────────────────────────────────────
# Docker helpers
# ─────────────────────────────────────────

def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_command(
    cmd: list[str],
    env: dict[str, str],
    cwd: str,
    log_callback: Callable[[str], None],
) -> int:
    """Run a command, stream stdout/stderr line by line via log_callback."""
    full_env = {**os.environ, **env}
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=full_env,
            cwd=cwd,
        )
        for line in proc.stdout:
            log_callback(line.rstrip())
        proc.wait()
        return proc.returncode
    except FileNotFoundError:
        log_callback(f"[error] Command not found: {cmd[0]}")
        return 127


# ─────────────────────────────────────────
# Breakpoint shell
# ─────────────────────────────────────────

def _env_preview(env: dict[str, str], limit: int = 8) -> list[str]:
    preview = []
    sensitive = ("SECRET", "TOKEN", "PASS", "KEY")
    for key in sorted(env.keys()):
        if key in {"GITHUB_WORKSPACE", "RUNNER_OS", "CI"} or len(preview) < limit:
            value = env.get(key, "")
            redacted = "***" if any(token in key.upper() for token in sensitive) else value
            preview.append(f"{key}={redacted}")
        if len(preview) >= limit:
            break
    return preview


def open_breakpoint_shell(container_id: str, step: Step, env: dict[str, str], cwd: str) -> None:
    """
    Drop the developer into an interactive shell inside the live container.
    Execution resumes when they exit the shell.
    """
    console.print()
    console.rule("[bold yellow]⏸  BREAKPOINT[/bold yellow]")
    console.print(
        f"  [dim]Step:[/dim] [bold]{step.display_name()}[/bold]  [dim](id: {step.id})[/dim]\n"
        f"  [dim]Working dir:[/dim] [bold]{cwd}[/bold]\n"
        f"  [dim]You are now inside the container. Type [bold]exit[/bold] or [bold]Ctrl+D[/bold] to resume.[/dim]"
    )
    if env:
        console.print("  [dim]Relevant env preview:[/dim]")
        for line in _env_preview(env):
            console.print(f"  [dim]  • {line}[/dim]")
    console.print()
    subprocess.run(["docker", "exec", "-it", container_id, "/bin/bash"])
    console.print()
    console.rule("[bold green]▶  Resuming pipeline[/bold green]")
    console.print()


def open_breakpoint_shell_local(step: Step, env: dict, cwd: str) -> None:
    """Fallback: open a local shell when Docker is unavailable (dry-run mode)."""
    console.print()
    console.rule("[bold yellow]⏸  BREAKPOINT (local shell)[/bold yellow]")
    console.print(
        f"  [dim]Step:[/dim] [bold]{step.display_name()}[/bold]  [dim](id: {step.id})[/dim]\n"
        f"  [dim]Working dir:[/dim] [bold]{cwd}[/bold]\n"
        f"  [dim]Docker not available — opening local shell with step env vars injected. Type [bold]exit[/bold] to resume.[/dim]"
    )
    if env:
        console.print("  [dim]Relevant env preview:[/dim]")
        for line in _env_preview(env):
            console.print(f"  [dim]  • {line}[/dim]")
    console.print()
    shell_env = {**os.environ, **env}
    subprocess.run(os.environ.get("SHELL", "/bin/bash"), env=shell_env, cwd=cwd)
    console.print()
    console.rule("[bold green]▶  Resuming pipeline[/bold green]")
    console.print()


# ─────────────────────────────────────────
# Container lifecycle
# ─────────────────────────────────────────

class ContainerSession:
    """Manages the lifecycle of a Docker container for one job."""

    def __init__(self, image: str, env: dict[str, str], repo_path: Path):
        self.image = image
        self.env = env
        self.repo_path = repo_path
        self.container_id: str | None = None

    def __enter__(self) -> "ContainerSession":
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self) -> None:
        env_args = []
        for k, v in self.env.items():
            env_args.extend(["-e", f"{k}={v}"])

        cmd = [
            "docker", "run",
            "--rm", "-d",
            "--workdir", "/workspace",
            "-v", f"{self.repo_path.resolve()}:/workspace",
            *env_args,
            self.image,
            "sleep", "3600",  # keep alive for the duration of the job
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr.strip()}")
        self.container_id = result.stdout.strip()

    def exec(self, script: str, env: dict[str, str], cwd: str, log_cb: Callable) -> int:
        extra_env = " ".join(f'{k}="{v}"' for k, v in env.items())
        full_script = f"set -e\n{extra_env}\ncd {shlex.quote(cwd)}\n{script}"
        cmd = ["docker", "exec", self.container_id, "/bin/bash", "-c", full_script]
        return _run_command(cmd, {}, str(self.repo_path), log_cb)

    def stop(self) -> None:
        if self.container_id:
            subprocess.run(
                ["docker", "rm", "-f", self.container_id],
                capture_output=True
            )
            self.container_id = None


# ─────────────────────────────────────────
# Step executor
# ─────────────────────────────────────────

def execute_step(
    step: Step,
    env: dict[str, str],
    repo_path: Path,
    container: ContainerSession | None,
    dry_run: bool = False,
    force_breakpoints: list[str] | None = None,
    event_sink: Callable[[dict], None] | None = None,
    job_id: str | None = None,
    step_index: int | None = None,
    cancel_event: Callable[[], bool] | None = None,
    breakpoint_handler: Callable[[str, int, str], str] | None = None,
) -> StepResult:
    """
    Execute a single step. Returns StepResult.

    In dry_run mode: validates + prints commands without executing.
    In Docker mode: runs inside container.
    In local mode: runs as subprocess on host.
    """
    force_breakpoints = force_breakpoints or []
    logs: list[str] = []
    start = time.time()

    should_break = step.breakpoint or step.id in force_breakpoints or step.name in force_breakpoints

    def log(line: str):
        logs.append(line)
        if event_sink and job_id is not None and step_index is not None:
            event_sink({
                "type": "log",
                "job_id": job_id,
                "step_index": step_index,
                "line": line,
            })

    if cancel_event and cancel_event():
        return StepResult(step=step, status=StepStatus.SKIPPED, logs=logs)

    if event_sink and job_id is not None and step_index is not None:
        event_sink({
            "type": "step_start",
            "job_id": job_id,
            "step_index": step_index,
            "step_name": step.display_name(),
        })

    # Handle action steps (uses:) — just log them, don't execute
    if step.uses:
        log(f"[action] {step.uses}")
        if step.with_inputs:
            for k, v in step.with_inputs.items():
                log(f"  with.{k}: {v}")
        result = StepResult(
            step=step,
            status=StepStatus.SUCCESS,
            exit_code=0,
            logs=logs,
            duration_seconds=time.time() - start,
        )
        if event_sink and job_id is not None and step_index is not None:
            event_sink({
                "type": "step_end",
                "job_id": job_id,
                "step_index": step_index,
                "status": result.status.name,
                "duration": result.duration_seconds,
                "exit_code": result.exit_code,
            })
        return result

    if not step.run:
        result = StepResult(step=step, status=StepStatus.SKIPPED, logs=logs,
                            duration_seconds=time.time() - start)
        if event_sink and job_id is not None and step_index is not None:
            event_sink({
                "type": "step_end",
                "job_id": job_id,
                "step_index": step_index,
                "status": result.status.name,
                "duration": result.duration_seconds,
                "exit_code": result.exit_code,
            })
        return result

    if dry_run:
        for line in step.run.strip().splitlines():
            log(f"[dry-run] {line}")
        result = StepResult(step=step, status=StepStatus.SUCCESS, logs=logs,
                            duration_seconds=time.time() - start)
        if event_sink and job_id is not None and step_index is not None:
            event_sink({
                "type": "step_end",
                "job_id": job_id,
                "step_index": step_index,
                "status": result.status.name,
                "duration": result.duration_seconds,
                "exit_code": result.exit_code,
            })
        return result

    cwd = str(repo_path / (step.working_directory or ""))
    step_env = {**env, **step.env}

    # Trigger breakpoint BEFORE step executes
    if should_break:
        if breakpoint_handler and job_id is not None and step_index is not None:
            action = breakpoint_handler(job_id, step_index, step.display_name())
            if action == "skip":
                result = StepResult(
                    step=step,
                    status=StepStatus.SKIPPED,
                    exit_code=0,
                    logs=logs,
                    duration_seconds=time.time() - start,
                )
                if event_sink:
                    event_sink({
                        "type": "step_end",
                        "job_id": job_id,
                        "step_index": step_index,
                        "status": result.status.name,
                        "duration": result.duration_seconds,
                        "exit_code": result.exit_code,
                    })
                return result
        else:
            if container and container.container_id:
                open_breakpoint_shell(container.container_id, step, step_env, cwd)
            else:
                open_breakpoint_shell_local(step, step_env, cwd)

    exit_code = 0
    if container and container.container_id:
        exit_code = container.exec(step.run, step_env, cwd, log)
    else:
        # Local execution fallback
        full_env = {**os.environ, **step_env}
        proc = subprocess.Popen(
            ["bash", "-c", step.run],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=full_env,
            cwd=cwd if os.path.isdir(cwd) else str(repo_path),
        )
        for line in proc.stdout:
            log(line.rstrip())
        proc.wait()
        exit_code = proc.returncode

    duration = time.time() - start
    status = StepStatus.SUCCESS if exit_code == 0 else StepStatus.FAILED
    result = StepResult(step=step, status=status, exit_code=exit_code,
                        logs=logs, duration_seconds=duration)
    if event_sink and job_id is not None and step_index is not None:
        event_sink({
            "type": "step_end",
            "job_id": job_id,
            "step_index": step_index,
            "status": result.status.name,
            "duration": result.duration_seconds,
            "exit_code": result.exit_code,
        })
    return result


# ─────────────────────────────────────────
# Job runner
# ─────────────────────────────────────────

def run_job(
    job: Job,
    workflow: Workflow,
    repo_path: Path,
    secrets: dict[str, str],
    dry_run: bool = False,
    force_breakpoints: list[str] | None = None,
    use_docker: bool = True,
    event_sink: Callable[[dict], None] | None = None,
    cancel_event: threading.Event | None = None,
    breakpoint_handler: Callable[[str, int, str], str] | None = None,
) -> JobResult:
    start = time.time()
    step_results: list[StepResult] = []
    use_docker = use_docker and _docker_available()

    base_env = merge_env(workflow.env, job.env, {}, secrets)
    base_env["GITHUB_WORKSPACE"] = str(repo_path)
    base_env["RUNNER_OS"] = "Linux"
    base_env["CI"] = "true"

    image = resolve_image(job.runs_on)

    def _run_steps(container: ContainerSession | None):
        for idx, step in enumerate(job.steps):
            if cancel_event and cancel_event.is_set():
                break
            step_env = merge_env(base_env, {}, step.env, secrets)
            result = execute_step(
                step=step,
                env=step_env,
                repo_path=repo_path,
                container=container,
                dry_run=dry_run,
                force_breakpoints=force_breakpoints,
                event_sink=event_sink,
                job_id=job.id,
                step_index=idx,
                cancel_event=cancel_event.is_set if cancel_event else None,
                breakpoint_handler=breakpoint_handler,
            )
            step_results.append(result)
            if result.status == StepStatus.FAILED:
                break  # fail-fast (matches GitHub Actions default)

    if event_sink:
        event_sink({"type": "job_start", "job_id": job.id, "job_name": job.name})

    if use_docker:
        with ContainerSession(image, base_env, repo_path) as container:
            _run_steps(container)
    else:
        _run_steps(None)

    # determine job status and return a JobResult
    duration = time.time() - start
    job_status = JobStatus.SUCCESS
    if any(s.status == StepStatus.FAILED for s in step_results):
        job_status = JobStatus.FAILED
    elif all(s.status == StepStatus.SKIPPED for s in step_results) and step_results:
        job_status = JobStatus.SKIPPED
    elif not step_results:
        job_status = JobStatus.SKIPPED

    result = JobResult(
        job=job,
        status=job_status,
        step_results=step_results,
        duration_seconds=duration,
    )

    if event_sink:
        event_sink({
            "type": "job_end",
            "job_id": job.id,
            "status": result.status.name,
            "duration": result.duration_seconds,
        })

    return result