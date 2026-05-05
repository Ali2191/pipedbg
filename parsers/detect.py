"""
pipedbg.parsers.detect
======================
Auto-detect workflow platform and dispatch to parsers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

from .base import Workflow
from ..auth.gate import ProFeatureError, feature_allowed
from .circleci import parse_circleci_workflow
from .github import parse_workflow
from .gitlab import parse_gitlab_workflow


Platform = Literal["github", "gitlab", "circleci", "unknown"]


def detect_platform(path: Path, raw: dict | None = None) -> Platform:
    name = path.name.lower()

    if ".github" in path.parts and "workflows" in path.parts:
        return "github"
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

    return "unknown"


def parse_any(path: Path) -> Workflow:
    platform = detect_platform(path)
    if platform in {"gitlab", "circleci"}:
        if not feature_allowed("multi_platform"):
            raise ProFeatureError("multi_platform")
    if platform == "gitlab":
        return parse_gitlab_workflow(path)
    if platform == "circleci":
        return parse_circleci_workflow(path)
    return parse_workflow(path)
