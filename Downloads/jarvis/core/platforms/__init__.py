"""
JARVIS Platforms
External system integrations for smart home, GitHub, and OS-level shortcuts.
"""

from .home_assistant import HomeAssistantClient
from .github_engine import create_issue as github_create_issue, push_repo as github_push_repo
from .hotkey_engine import hotkey_engine

__all__ = [
    "HomeAssistantClient",
    "github_create_issue",
    "github_push_repo",
    "hotkey_engine",
]
