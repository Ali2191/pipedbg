"""
Compatibility shim for parsers.
"""
from __future__ import annotations

from pathlib import Path

from .parsers import (
    Job,
    Step,
    Workflow,
    WorkflowParseError,
    detect_platform,
    parse_any,
    parse_circleci_workflow,
    parse_gitlab_workflow,
    parse_workflow,
)


def parse_pipeline(path: str | Path) -> Workflow:
    return parse_any(Path(path))


def find_workflows(repo_root: str | Path) -> list[Path]:
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


__all__ = [
    "Job",
    "Step",
    "Workflow",
    "WorkflowParseError",
    "parse_workflow",
    "parse_gitlab_workflow",
    "parse_circleci_workflow",
    "detect_platform",
    "parse_any",
    "parse_pipeline",
    "find_workflows",
]
