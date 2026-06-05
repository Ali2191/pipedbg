"""
pipedbg CLI
===========
Run, inspect, and debug CI/CD workflows locally.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib
from datetime import datetime
from pathlib import Path
from urllib import request

import click
from rich.console import Console

from .display import (
    console,
    print_breakpoints_found,
    print_failure_panel,
    print_job_result,
    print_summary,
    print_workflow_header,
)
from .parser import WorkflowParseError, find_workflows, parse_pipeline
from .runner import JobStatus, StepStatus, load_secrets, run_job
from . import ai
from .auth import (
    LicenseError,
    ProFeatureError,
    UsageLimitError,
    check_ai_limit,
    get_license,
    is_pro,
    render_pro_message,
    save_license,
)
from .auth.limits import record_audit_log
from .auth.gate import require_pro
from .web.server import run_ui_server


def _infer_repo_path(workflow_file: Path) -> Path:
    parts = workflow_file.parts
    if ".github" in parts and "workflows" in parts:
        try:
            idx = parts.index(".github")
            return Path(*parts[:idx])
        except Exception:
            pass
    return workflow_file.parent


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> dict:
    url = f"http://{host}:{port}/api/session"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("UI server did not start in time.")


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


@click.group()
@click.version_option("0.2.0", prog_name="pipedbg")
def cli() -> None:
    """pipedbg — debug CI/CD pipelines locally."""


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--job", "jobs", multiple=True, help="Run only specific job(s).")
@click.option("--dry-run", is_flag=True, help="Parse and print commands without executing.")
@click.option("--break-on", "break_on", multiple=True, help="Force a breakpoint by name or ID.")
@click.option("--env-file", type=click.Path(path_type=Path), default=None, help="Path to a .env file.")
@click.option("--no-docker", is_flag=True, help="Run on host instead of Docker.")
@click.option("--verbose", is_flag=True, help="Show full step logs.")
@click.option("--apply-ai-fix", is_flag=True, help="Offer to apply AI YAML changes.")
@click.option("--repo", type=click.Path(path_type=Path), default=None, help="Repository root.")
@click.option("--ui", is_flag=True, help="Open the web UI and stream this run.")
@click.option("--notify", type=str, default=None, help="Webhook URL to notify on pass/fail (Pro).")
@click.option("--ui-host", default="127.0.0.1", show_default=True)
@click.option("--ui-port", default=7337, show_default=True, type=int)
def run(
    workflow_file: Path,
    jobs: tuple[str, ...],
    dry_run: bool,
    break_on: tuple[str, ...],
    env_file: Path | None,
    no_docker: bool,
    verbose: bool,
    apply_ai_fix: bool,
    repo: Path | None,
    ui: bool,
    notify: str | None,
    ui_host: str,
    ui_port: int,
) -> None:
    """Run a workflow file locally."""
    repo_path = repo or _infer_repo_path(workflow_file)

    if ui:
        thread = threading.Thread(
            target=run_ui_server,
            kwargs={
                "workflow_path": workflow_file,
                "repo_path": repo_path,
                "host": ui_host,
                "port": ui_port,
                "open_browser": True,
                "share": False,
            },
            daemon=True,
        )
        thread.start()
        session = _wait_for_server(ui_host, ui_port)
        _post_json(f"http://{ui_host}:{ui_port}/api/run", {
            "session_id": session.get("session_id"),
            "workflow": str(workflow_file),
            "dry_run": dry_run,
            "env_file": str(env_file) if env_file else None,
        })
        console.print("[green]UI server running. Press Ctrl+C to stop.[/green]")
        try:
            thread.join()
        except KeyboardInterrupt:
            console.print("\n[dim]UI server stopped.[/dim]")
        return

    try:
        workflow = parse_pipeline(workflow_file)
    except (WorkflowParseError, FileNotFoundError, ProFeatureError) as e:
        console.print(f"[red bold]Parse error:[/red bold] {e}")
        sys.exit(1)

    env_file = env_file or (repo_path / ".env")
    secrets = load_secrets(env_file)
    if secrets:
        console.print(f"[dim]Loaded {len(secrets)} secret(s) from {env_file}[/dim]")

    mode_tags = []
    if dry_run:
        mode_tags.append("[yellow]dry-run[/yellow]")
    if no_docker:
        mode_tags.append("[dim]no-docker[/dim]")
    if break_on:
        mode_tags.append(f"[yellow]break-on: {', '.join(break_on)}[/yellow]")

    console.print()
    console.print(
        f"[bold]pipedbg[/bold]  {workflow.name}"
        + (f"  {' '.join(mode_tags)}" if mode_tags else "")
    )
    print_workflow_header(workflow)
    print_breakpoints_found(workflow)

    try:
        execution_order = workflow.execution_order()
    except WorkflowParseError as e:
        console.print(f"[red bold]Error:[/red bold] {e}")
        sys.exit(1)

    selected_jobs = list(jobs) if jobs else execution_order
    unknown = [j for j in selected_jobs if j not in workflow.jobs]
    if unknown:
        console.print(f"[red bold]Unknown job(s):[/red bold] {', '.join(unknown)}")
        console.print(f"Available: {', '.join(workflow.jobs.keys())}")
        sys.exit(1)

    r