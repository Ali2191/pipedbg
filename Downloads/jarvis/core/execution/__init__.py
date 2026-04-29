"""
JARVIS Execution
Task execution threads, persistence, and audio output.
"""

from .persistent_executor import PersistentExecutor
from .tts_engine import VOICE_PROFILES

__all__ = [
    "PersistentExecutor",
    "VOICE_PROFILES",
]
