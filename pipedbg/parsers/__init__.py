"""
Parsers for GitHub Actions, GitLab CI, and CircleCI.
"""
from .base import Job, Step, Workflow, WorkflowParseError
from .github import parse_workflow
from .gitlab import parse_gitlab_workflow
from .circleci import parse_circleci_workflow
from .detect import detect_platform, parse_any

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
]
