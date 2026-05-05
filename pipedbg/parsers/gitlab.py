"""
GitLab CI parser.
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

_RESERVED_GITLAB_KEYS = {
    "stages",
    "variables",
    "default",
    "workflow",
    "include",
    "image",
    "services",
    "before_script",
    "after_script",
    "cache",
    "pages",
}


def _script_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [resolve_string(v) for v in value]
    return []


def _steps_from_script(job_id: str, label: str, script: Any, offset: int = 0) -> list[Step]:
    lines = _script_lines(script)
    steps: list[Step] = []
    for idx, line in enumerate(lines):
        run, has_breakpoint = strip_breakpoint(line)
        steps.append(
            Step(
                id=f"{job_id}-{label}-{idx + 1 + offset}",
                name=f"{label}[{idx + 1}]",
                run=run,
                breakpoint=has_breakpoint,
            )
        )
    return steps


def parse_gitlab_workflow(path: str | Path) -> Workflow:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowParseError(f"YAML parse error in {path.name}: {e}")

    if not isinstance(raw, dict):
        raise WorkflowParseError(f"{path.name} is not a valid GitLab CI file.")

    stages = raw.get("stages", [])
    stage_order = {stage: idx for idx, stage in enumerate(stages)} if isinstance(stages, list) else {}

    global_before = raw.get("before_script", [])
    global_after = raw.get("after_script", [])
    global_env = parse_env(raw.get("variables", {}))
    global_services = raw.get("services", [])
    global_image = raw.get("image")

    jobs: dict[str, Job] = {}
    for key, value in raw.items():
        if key in _RESERVED_GITLAB_KEYS or not isinstance(value, dict):
            continue
        if "script" not in value:
            continue

        job_id = str(key)
        stage_name = resolve_string(value.get("stage", "test")) or "test"
        needs_raw = value.get("needs", [])
        needs: list[str] = []
        if isinstance(needs_raw, list):
            for dep in needs_raw:
                if isinstance(dep, dict):
                    dep_job = dep.get("job")
                    if dep_job:
                        needs.append(resolve_string(dep_job))
                else:
                    needs.append(resolve_string(dep))
        elif isinstance(needs_raw, str):
            needs = [needs_raw]

        job_before = value.get("before_script", [])
        job_after = value.get("after_script", [])
        steps: list[Step] = []
        steps.extend(_steps_from_script(job_id, "before_script", global_before))
        steps.extend(_steps_from_script(job_id, "before_script", job_before, offset=len(steps)))
        steps.extend(_steps_from_script(job_id, "script", value.get("script", []), offset=len(steps)))
        steps.extend(_steps_from_script(job_id, "after_script", job_after, offset=len(steps)))
        steps.extend(_steps_from_script(job_id, "after_script", global_after, offset=len(steps)))

        job_image = value.get("image", global_image)
        image = resolve_string(job_image) if job_image is not None else "gitlab-runner"

        job_env = parse_env(value.get("variables", {}))
        env = {**global_env, **job_env}

        job_services = value.get("services", global_services)
        rules = value.get("rules", [])
        only = value.get("only")
        except_ = value.get("except")

        jobs[job_id] = Job(
            id=job_id,
            name=resolve_string(value.get("name", job_id)) or job_id,
            runs_on=image,
            steps=steps,
            needs=needs,
            env=env,
            services=job_services,
            stage=stage_name,
            rules=rules if isinstance(rules, list) else [rules] if rules else [],
            only=only if isinstance(only, list) else [only] if only else None,
            except_=except_ if isinstance(except_, list) else [except_] if except_ else None,
            strategy={
                "stage": stage_name,
                "stage_index": stage_order.get(stage_name, 9999),
            },
            image=image,
        )

    if not jobs:
        raise WorkflowParseError(f"{path.name} has no executable GitLab jobs.")

    # If explicit needs are not provided, infer stage-based dependencies.
    stage_to_jobs: dict[str, list[str]] = {}
    for job in jobs.values():
        stage_to_jobs.setdefault(job.stage or "test", []).append(job.id)

    ordered_stages = sorted(
        stage_to_jobs.keys(),
        key=lambda s: jobs[stage_to_jobs[s][0]].strategy.get("stage_index", 9999),
    )
    prev_stage_jobs: list[str] = []
    for stage_name in ordered_stages:
        current_jobs = stage_to_jobs[stage_name]
        for job_id in current_jobs:
            if not jobs[job_id].needs and prev_stage_jobs:
                jobs[job_id].needs = list(prev_stage_jobs)
        prev_stage_jobs = current_jobs

    return Workflow(
        name=resolve_string(raw.get("workflow", {}).get("name", path.stem)) or path.stem,
        path=path,
        on_triggers={},
        env=global_env,
        jobs=jobs,
        dag=build_dag(jobs),
        platform="gitlab",
    )
