"""
Feature gating utilities for pipedbg Pro.
"""
from __future__ import annotations

from functools import wraps
from typing import Callable, TypeVar

from rich.console import Console

from .license import get_license, is_pro

console = Console()

PRO_UPGRADE_URL = "https://pipedbg.dev/pro"


def render_pro_message(feature_name: str) -> str:
    inner_width = 42

    def line(text: str) -> str:
        return "│ " + text.ljust(inner_width - 2) + " │"

    return "\n".join(
        [
            "╭─ Pro Feature ────────────────────────────╮",
            line(f"{feature_name} requires pipedbg Pro"),
            line(""),
            line("Free tier: 10 AI explanations/day"),
            line("Pro ($12/mo): unlimited + team sharing"),
            line(""),
            line(f"Upgrade: {PRO_UPGRADE_URL}"),
            line("Already have a key? pipedbg auth login"),
            "╰──────────────────────────────────────────╯",
        ]
    )


class ProFeatureError(Exception):
    def __init__(self, feature_name: str):
        super().__init__(feature_name)
        self.feature_name = feature_name


F = TypeVar("F", bound=Callable)


def require_pro(feature_name: str) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not is_pro():
                raise ProFeatureError(feature_name)
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def feature_allowed(feature_name: str) -> bool:
    lic = get_license()
    if not lic:
        return False
    if lic.tier.lower() == "pro":
        return True
    return feature_name in (lic.features or [])


def get_feature_list() -> list[str]:
    lic = get_license()
    if not lic:
        return []
    return list(lic.features or [])
