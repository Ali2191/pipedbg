"""
pipedbg.parser
==============
Parses CI workflow definitions into a common internal graph.

Supported platforms:
- GitHub Actions
- GitLab CI
- CircleCI
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import yaml


@dataclass
class Step:
    id: str
    name: str
    run: str | None = None
    uses: str | None = None
    with_inputs: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    if_condition: str | None = None
    breakpoint: bool = False
    working_directory: str | None = None
    shell: str = "bash"

    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.uses:
            return f"uses: {self.uses}"
        if self.run:
            first_line = self.run.strip().splitlines()[0]
            return first_line[:60] + ("..." if len(first_line) > 60 else "")
        return self.id


@dataclass
class Job:
    id: str
    name: str
    runs_on: str
    steps: list[Step] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    if_condition: str | None = None
    timeout_minutes: int = 360
    strategy: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    services: dict = field(default_factory=dict)


@dataclass
class Workflow:
    name: str
    path: Path
    on_triggers: dict
    env: dict
    jobs: dict[str, Job]
    dag: nx.DiGraph = field(default_factory=nx.DiGraph)
    platform: str = "github"

    def execution_order(self) -> list[str]:
        try:
            return list(nx.topological_sort(self.dag))
        except nx.NetworkXUnfeasible:
            raise WorkflowParseError("Circular dependency detected in job graph.")

    def job_levels(self) -> list[list[str]]:
        levels: list[list[str]] = []
        remaining = set(self.jobs.keys())
        resolved: set[str] = set()

        while remaining:
            level = [
                job_id
                for job_id in remaining
                if all(dep in resolved for dep in self.jobs[job_id].needs)
            ]
            if not level:
                raise WorkflowParseError("Could not resolve job levels - check for cycles.")
            levels.append(sorted(level))
            resolved.update(level)
            remaining -= set(level)

        return levels


class WorkflowParseError(Exception):
    pass


_BREAKPOINT_PATTERN = re.compile(r"#\s*breakpoint", re.IGNORECASE)
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
}


def _resolve_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _parse_env(raw: Any) -> dict:
    if not raw or not isinstance(raw, dict):
        return {}
    return {str(k): _resolve_string(v) for k, v in raw.items()}


def _strip_breakpoint(run_script: str | None) -> tuple[str | None, bool]:
    if not run_script:
        return run_script, False
    if not _BREAKPOINT_PATTERN.search(run_script):
        return run_script, False

    cleaned = "\n".join(
        line for line in run_script.splitlines() if not _BREAKPOINT_PATTERN.search(line)
    ).strip()
    return cleaned, True


def _build_dag(jobs: dict[str, Job]) -> nx.DiGraph:
    dag = nx.DiGraph()
    dag.add_nodes_from(jobs.keys())
    for job_id, job in jobs.items():
        for dep in job.needs:
            if dep not in jobs:
                raise WorkflowParseError(
                    f"Job `{job_id}` depends on `{dep}`, but `{dep}` is not defined."
                )
            dag.add_edge(dep, job_id)
    return dag


def _parse_github_step(raw: dict, idx: int) -> Step:
    step_id = _resolve_string(raw.get("id", f"step-{idx + 1}"))
    run_script = raw.get("run")
    run_script, has_breakpoint = _strip_breakpoint(run_script)
    with_raw = raw.get("with", {})

    return Step(
        id=step_id,
        name=_resolve_string(raw.get("name", "")),
        run=run_script if run_script is not None else None,
        uses=_resolve_string(raw.get("uses", "")) or None,
        with_inputs=with_raw if isinstance(with_raw, dict) else {},
        env=_parse_env(raw.get("env")),
        if_condition=_resolve_string(raw.get("if", "")) or None,
        breakpoint=has_breakpoint,
        working_directory=_resolve_string(raw.get("working-directory", "")) or None,
        shell=_resolve_string(raw.get("shell", "bash")),
    )


def _parse_github_job(job_id: str, raw: dict) -> Job:
    if not isinstance(raw, dict):
        raise WorkflowParseError(f"Job `{job_id}` is malformed - expected a mapping.")

    runs_on_raw = raw.get("runs-on", "ubuntu-latest")
    runs_on = " ".join(runs_on_raw) if isinstance(runs_on_raw, list) else _resolve_string(runs_on_raw)

    needs_raw = raw.get("needs", [])
    if isinstance(needs_raw, str):
        needs = [needs_raw]
    elif isinstance(needs_raw, list):
        needs = [_resolve_string(n) for n in needs_raw]
    else:
        needs = []

    raw_steps = raw.get("steps", [])
    if not isinstance(raw_steps, list):
        raise WorkflowParseError(f"Job `{job_id}`: `steps` must be a list.")

    steps = [_parse_github_step(step, idx) for idx, step in enumerate(raw_steps) if isinstance(step, dict)]

    return Job(
        id=job_id,
        name=_resolve_string(raw.get("name", job_id)),
        runs_on=runs_on,
        steps=steps,
        needs=needs,
        env=_parse_env(raw.get("env")),
        if_condition=_resolve_string(raw.get("if", "")) or None,
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

    jobs = {str(job_id): _parse_github_job(str(job_id), job_raw) for job_id, job_raw in raw_jobs.items()}
    dag = _build_dag(jobs)

    on_raw = raw.get("on", raw.get("true", {}))
    if isinstance(on_raw, bool):
        on_raw = {}

    return Workflow(
        name=_resolve_string(raw.get("name", path.stem)),
        path=path,
        on_triggers=on_raw if isinstance(on_raw, dict) else {},
        env=_parse_env(raw.get("env")),
        jobs=jobs,
        dag=dag,
        platform="github",
    )


def _parse_gitlab_script_steps(script: Any, job_id: str) -> list[Step]:
    if isinstance(script, str):
        script_lines = [script]
    elif isinstance(script, list):
        script_lines = [_resolve_string(line) for line in script]
    else:
        script_lines = []

    steps: list[Step] = []
    for idx, line in enumerate(script_lines):
        run, has_breakpoint = _strip_breakpoint(line)
        steps.append(
            Step(
                id=f"{job_id}-step-{idx + 1}",
                name=f"script[{idx + 1}]",
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

    jobs: dict[str, Job] = {}
    for key, value in raw.items():
        if key in _RESERVED_GITLAB_KEYS or not isinstance(value, dict):
            continue
        if "script" not in value:
            continue

        job_id = str(key)
        stage_name = _resolve_string(value.get("stage", "test"))
        needs_raw = value.get("needs", [])
        needs: list[str] = []
        if isinstance(needs_raw, list):
            for dep in needs_raw:
                if isinstance(dep, dict):
                    dep_job = dep.get("job")
                    if dep_job:
                        needs.append(_resolve_string(dep_job))
                else:
                    needs.append(_resolve_string(dep))

        steps = _parse_gitlab_script_steps(value.get("script", []), job_id)
        jobs[job_id] = Job(
            id=job_id,
            name=job_id,
            runs_on="gitlab-runner",
            steps=steps,
            needs=needs,
            env=_parse_env(raw.get("variables", {})) | _parse_env(value.get("variables", {})),
            strategy={"stage": stage_name, "stage_index": stage_order.get(stage_name, 9999)},
        )

    if not jobs:
        raise WorkflowParseError(f"{path.name} has no executable GitLab jobs.")

    # If explicit needs are not provided, infer stage-based dependencies.
    stage_to_jobs: dict[str, list[str]] = {}
    for job in jobs.values():
        stage_name = _resolve_string(job.strategy.get("stage", "test"))
        stage_to_jobs.setdefault(stage_name, []).append(job.id)

    ordered_stages = sorted(stage_to_jobs.keys(), key=lambda s: jobs[stage_to_jobs[s][0]].strategy.get("stage_index", 9999))
    prev_stage_jobs: list[str] = []
    for stage_name in ordered_stages:
        current_jobs = stage_to_jobs[stage_name]
        for job_id in current_jobs:
            if not jobs[job_id].needs and prev_stage_jobs:
                jobs[job_id].needs = list(prev_stage_jobs)
        prev_stage_jobs = current_jobs

    return Workflow(
        name=_resolve_string(raw.get("workflow", {}).get("name", path.stem)) or path.stem,
        path=path,
        on_triggers={},
        env=_parse_env(raw.get("variables", {})),
        jobs=jobs,
        dag=_build_dag(jobs),
        platform="gitlab",
    )


def _parse_circleci_steps(job_id: str, raw_steps: Any) -> list[Step]:
    steps: list[Step] = []
    if not isinstance(raw_steps, list):
        return steps

    for idx, step in enumerate(raw_steps):
        if isinstance(step, str):
            steps.append(
                Step(
                    id=f"{job_id}-step-{idx + 1}",
                    name=step,
                    uses=step,
                )
            )
            continue

        if not isinstance(step, dict):
            continue

        if "run" in step:
            run_val = step.get("run")
            run_script = run_val if isinstance(run_val, str) else _resolve_string(run_val.get("command", ""))
            run_script, has_breakpoint = _strip_breakpoint(run_script)
            step_name = _resolve_string(step.get("name", ""))
            if not step_name and isinstance(run_val, dict):
                step_name = _resolve_string(run_val.get("name", ""))
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
                name=_resolve_string(key),
                uses=_resolve_string(key),
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
                workflow_name = _resolve_string(key)
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
            job_name = _resolve_string(next(iter(item.keys()), ""))
            if not job_name:
                continue
            workflow_job_names.append(job_name)
            job_cfg = item.get(job_name, {})
            requires = job_cfg.get("requires", []) if isinstance(job_cfg, dict) else []
            needs_map[job_name] = [_resolve_string(dep) for dep in requires] if isinstance(requires, list) else []

    jobs: dict[str, Job] = {}
    for job_name in workflow_job_names:
        definition = jobs_def.get(job_name, {})
        if not isinstance(definition, dict):
            continue
        docker_def = definition.get("docker", [])
        image = "circleci"
        if isinstance(docker_def, list) and docker_def and isinstance(docker_def[0], dict):
            image = _resolve_string(docker_def[0].get("image", "circleci"))

        jobs[job_name] = Job(
            id=job_name,
            name=job_name,
            runs_on=image,
            steps=_parse_circleci_steps(job_name, definition.get("steps", [])),
            needs=needs_map.get(job_name, []),
            env=_parse_env(definition.get("environment", {})),
        )

    if not jobs:
        raise WorkflowParseError(f"{path.name} has no executable CircleCI workflow jobs.")

    return Workflow(
        name=workflow_name,
        path=path,
        on_triggers={},
        env={},
        jobs=jobs,
        dag=_build_dag(jobs),
        platform="circleci",
    )


def detect_platform(path: str | Path, raw: dict[str, Any] | None = None) -> str:
    path = Path(path)
    name = path.name.lower()

    if name == ".gitlab-ci.yml":
        return "gitlab"
    if ".circleci" in path.parts and name in {"config.yml", "config.yaml"}:
        return "circleci"

    if raw is None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

    if isinstance(raw, dict):
        if "workflows" in raw and "jobs" in raw and "version" in raw:
            return "circleci"
        if "stages" in raw or any(k in raw for k in ("before_script", "after_script", "variables")):
            if "jobs" not in raw:
                return "gitlab"

    return "github"


def parse_pipeline(path: str | Path) -> Workflow:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    platform = detect_platform(path)
    if platform == "gitlab":
        return parse_gitlab_workflow(path)
    if platform == "circleci":
        return parse_circleci_workflow(path)
    return parse_workflow(path)


def find_workflows(repo_root: str | Path) -> list[Path]:
    """Discover workflow/config files across supported platforms."""
    root = Path(repo_root)
    discovered: set[Path] = set()

    gh_dir = root / ".github" / "workflows"
    if gh_dir.exists():
        for p in gh_dir.iterdir():
            if p.is_file() and p.suffix in (".yml", ".yaml"):
                discovered.add(p)

    gitlab_file = root / ".gitlab-ci.yml"
    if gitlab_file.exists() and gitlab_file.is_file():
        discovered.add(gitlab_file)

    circleci_file = root / ".circleci" / "config.yml"
    if circleci_file.exists() and circleci_file.is_file():
        discovered.add(circleci_file)

    circleci_file_alt = root / ".circleci" / "config.yaml"
    if circleci_file_alt.exists() and circleci_file_alt.is_file():
        discovered.add(circleci_file_alt)

    return sorted(discovered)
