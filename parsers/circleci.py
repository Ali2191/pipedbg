"""
CircleCI config parser.
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

_BUILTIN_STEPS = {
    "checkout",
    "setup_remote_docker",
    "store_artifacts",
    "store_test_results",
}


def _parse_circleci_steps(job_id: str, raw_steps: Any) -> list[Step]:
    steps: list[Step] = []
    if not isinstance(raw_steps, list):
        return steps

    for idx, step in enumerate(raw_steps):
        if isinstance(step, str):
            name = resolve_string(step)
            uses = name if name in _BUILTIN_STEPS else name
            steps.append(
                Step(
                    id=f"{job_id}-step-{idx + 1}",
                    name=name,
                    uses=uses,
                )
            )
            continue

        if not isinstance(step, dict):
            continue

        if "run" in step:
            run_val = step.get("run")
            if isinstance(run_val, str):
                run_script = run_val
                step_name = resolve_string(step.get("name", ""))
            elif isinstance(run_val, dict):
                run_script = resolve_string(run_val.get("command", ""))
                step_name = resolve_string(run_val.get("name", ""))
            else:
                run_script = ""
                step_name = ""

            run_script, has_breakpoint = strip_breakpoint(run_script)
            steps.append(
                Step(
                    id=f"{job_id}-step-{idx + 1}",
                    name=step_name or f"run[{idx + 1}]",
                    run=run_script,
                    breakpoint=has_breakpoint,
                )
            )
            continue

        # Other built-in CircleCI keys (checkout, setup_remote_docker, etc.)
        key = next(iter(step.keys()), f"step-{idx + 1}")
        steps.append(
            Step(
                id=f"{job_id}-step-{idx + 1}",
                name=resolve_string(key),
                uses=resolve_string(key),
            )
        )

    return steps


def parse_circleci_workflow(path: str | Path) -> Workflow:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowParseError(f"YAML parse error in {path.name}: {e}")

    if not isinstance(raw, dict):
        raise WorkflowParseError(f"{path.name} is not a valid CircleCI config.")

    version = raw.get("version")
    if not version:
        raise WorkflowParseError(f"{path.name} is missing CircleCI version.")

    if isinstance(version, (int, float)) and version < 2:
        raise WorkflowParseError(f"{path.name} requires CircleCI version 2.1 or higher.")
    if isinstance(version, str) and not version.startswith("2"):
        raise WorkflowParseError(f"{path.name} requires CircleCI version 2.1 or higher.")

    jobs_def = raw.get("jobs", {})
    workflows_def = raw.get("workflows", {})
    if not isinstance(jobs_def, dict) or not jobs_def:
        raise WorkflowParseError(f"{path.name} has no CircleCI jobs.")

    workflow_name = "workflow"
    workflow_jobs: list[Any] = []
    if isinstance(workflows_def, dict):
        for key, val in workflows_def.items():
            if key == "version":
                continue
            if isinstance(val, dict) and isinstance(val.get("jobs"), list):
                workflow_name = resolve_string(key)
                workflow_jobs = val["jobs"]
                break

    # Fallback when workflow section is missing: include all jobs with no dependencies.
    if not workflow_jobs:
        workflow_jobs = [str(job_name) for job_name in jobs_def.keys()]

    needs_map: dict[str, list[str]] = {}
    workflow_job_names: list[str] = []
    for item in workflow_jobs:
        if isinstance(item, str):
            workflow_job_names.append(item)
            needs_map[item] = []
        elif isinstance(item, dict):
            job_name = resolve_string(next(iter(item.keys()), ""))
            if not job_name:
                continue
            workflow_job_names.append(job_name)
            job_cfg = item.get(job_name, {})
            requires = job_cfg.get("requires", []) if isinstance(job_cfg, dict) else []
            needs_map[job_name] = [resolve_string(dep) for dep in requires] if isinstance(requires, list) else []

    jobs: dict[str, Job] = {}
    for job_name in workflow_job_names:
        definition = jobs_def.get(job_name, {})
        if not isinstance(definition, dict):
            continue

        docker_def = definition.get("docker", [])
        image = "circleci"
        executor = "docker"
        if isinstance(docker_def, list) and docker_def and isinstance(docker_def[0], dict):
            image = resolve_string(docker_def[0].get("image", "circleci"))
        elif "machine" in definition:
            executor = "machine"
            machine = definition.get("machine", {})
            if isinstance(machine, dict):
                image = resolve_string(machine.get("image", "machine"))
            else:
                image = "machine"

        jobs[job_name] = Job(
            id=job_name,
            name=job_name,
            runs_on=image,
            steps=_parse_circleci_steps(job_name, definition.get("steps", [])),
            needs=needs_map.get(job_name, []),
            env=parse_env(definition.get("environment", {})),
            parameters=definition.get("parameters", {}) if isinstance(definition.get("parameters"), dict) else {},
            executor=executor,
            image=image,
        )

    if not jobs:
        raise WorkflowParseError(f"{path.name} has no executable CircleCI workflow jobs.")

    return Workflow(
        name=workflow_name,
        path=path,
        on_triggers={},
        env={},
        jobs=jobs,
        dag=build_dag(jobs),
        platform="circleci",
    )
