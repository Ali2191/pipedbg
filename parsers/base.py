"""
pipedbg.parsers.base
====================
Shared workflow data structures and helpers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx


class WorkflowParseError(Exception):
    pass


_BREAKPOINT_PATTERN = re.compile(r"#\s*breakpoint", re.IGNORECASE)


def resolve_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def parse_env(raw: Any) -> dict[str, str]:
    if not raw or not isinstance(raw, dict):
        return {}
    return {str(k): resolve_string(v) for k, v in raw.items()}


def strip_breakpoint(run_script: str | None) -> tuple[str | None, bool]:
    if not run_script:
        return run_script, False
    if not _BREAKPOINT_PATTERN.search(run_script):
        return run_script, False

    cleaned = "\n".join(
        line for line in run_script.splitlines() if not _BREAKPOINT_PATTERN.search(line)
    ).strip()
    return cleaned, True


def build_dag(jobs: dict[str, "Job"]) -> nx.DiGraph:
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


@dataclass
class Step:
    id: str
    name: str
    run: str | None = None
    uses: str | None = None
    with_inputs: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
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
    env: dict[str, str] = field(default_factory=dict)
    if_condition: str | None = None
    timeout_minutes: int = 360
    strategy: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    services: Any = field(default_factory=dict)
    stage: str | None = None
    rules: list[Any] = field(default_factory=list)
    only: list[str] | None = None
    except_: list[str] | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    executor: str | None = None
    image: str | None = None
    platform_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    name: str
    path: Path
    on_triggers: dict[str, Any]
    env: dict[str, str]
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
