"""
GitHub Actions workflow parser.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .base import (
    Job,
    Step,
    Workflow,
    WorkflowParseError,
    build_dag,
    parse_env,
    resolve_string,
    strip_breakpoint,
)


def _parse_github_step(raw: dict, idx: int) -> Step:
    step_id = resolve_string(raw.get("id", f"step-{idx + 1}"))
    run_script = raw.get("run")
    run_script, has_breakpoint = strip_breakpoint(run_script)
    with_raw = raw.get("with", {})

    return Step(
        id=step_id,
        name=resolve_string(raw.get("name", "")),
        run=run_script if run_script is not None else None,
        uses=resolve_string(raw.get("uses", "")) or None,
        with_inputs=with_raw if isinstance(with_raw, dict) else {},
        env=parse_env(raw.get("env")),
        if_condition=resolve_string(raw.get("if", "")) or None,
        breakpoint=has_breakpoint,
        working_directory=resolve_string(raw.get("working-directory", "")) or None,
        shell=resolve_string(raw.get("shell", "bash")),
    )


def _parse_github_job(job_id: str, raw: dict) -> Job:
    if not isinstance(raw, dict):
        raise WorkflowParseError(f"Job `{job_id}` is malformed - expected a mapping.")

    runs_on_raw = raw.get("runs-on", "ubuntu-latest")
    runs_on = " ".join(runs_on_raw) if isinstance(runs_on_raw, list) else resolve_string(runs_on_raw)

    needs_raw = raw.get("needs", [])
    if isinstance(needs_raw, str):
        needs = [needs_raw]
    elif isinstance(needs_raw, list):
        needs = [resolve_string(n) for n in needs_raw]
    else:
        needs = []

    raw_steps = raw.get("steps", [])
    if not isinstance(raw_steps, list):
        raise WorkflowParseError(f"Job `{job_id}`: `steps` must be a list.")

    steps = [
        _parse_github_step(step, idx)
        for idx, step in enumerate(raw_steps)
        if isinstance(step, dict)
    ]

    return Job(
        id=job_id,
        name=resolve_string(raw.get("name", job_id)),
        runs_on=runs_on,
        steps=steps,
        needs=needs,
        env=parse_env(raw.get("env")),
        if_condition=resolve_string(raw.get("if", "")) or None,
        timeout_minutes=int(raw.get("timeout-minutes", 360)),
        strategy=raw.get("strategy", {}),
        outputs=raw.get("outputs", {}),
        services=raw.get("services", {}),
    )


def parse_workflow(path: str | Path) -> Workflow:
    """Parse a GitHub Actions workflow YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowParseError(f"YAML parse error in {path.name}: {e}")

    if not isinstance(raw, dict):
        raise WorkflowParseError(f"{path.name} is not a valid workflow file.")

    raw_jobs = raw.get("jobs", {})
    if not isinstance(raw_jobs, dict) or not raw_jobs:
        raise WorkflowParseError(f"{path.name} has no jobs defined.")

    jobs = {
        str(job_id): _parse_github_job(str(job_id), job_raw)
        for job_id, job_raw in raw_jobs.items()
    }
    dag = build_dag(jobs)

    on_raw = raw.get("on", raw.get("true", {}))
    if isinstance(on_raw, bool):
        on_raw = {}

    return Workflow(
        name=resolve_string(raw.get("name", path.stem)),
        path=path,
        on_triggers=on_raw if isinstance(on_raw, dict) else {},
        env=parse_env(raw.get("env")),
        jobs=jobs,
        dag=dag,
        platform="github",
    )
