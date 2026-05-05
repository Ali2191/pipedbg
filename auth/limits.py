"""
Usage tracking and limits for free tier.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .license import is_pro

USAGE_DIR = Path.home() / ".pipedbg"
USAGE_PATH = USAGE_DIR / "usage.json"
AUDIT_PATH = USAGE_DIR / "audit_log.jsonl"

FREE_AI_LIMIT = 10


class UsageLimitError(Exception):
    pass


@dataclass
class UsageState:
    date: str
    ai_calls: int


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _load_usage() -> UsageState:
    if not USAGE_PATH.exists():
        return UsageState(date=_today(), ai_calls=0)
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return UsageState(date=str(data.get("date", _today())), ai_calls=int(data.get("ai_calls", 0)))
    except Exception:
        return UsageState(date=_today(), ai_calls=0)


def _save_usage(state: UsageState) -> None:
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_PATH.write_text(json.dumps({"date": state.date, "ai_calls": state.ai_calls}, indent=2), encoding="utf-8")


def check_ai_limit() -> None:
    if is_pro():
        return

    state = _load_usage()
    today = _today()
    if state.date != today:
        state = UsageState(date=today, ai_calls=0)

    if state.ai_calls >= FREE_AI_LIMIT:
        raise UsageLimitError("AI explain limit reached for free tier.")

    state.ai_calls += 1
    _save_usage(state)


def get_usage_state() -> UsageState:
    state = _load_usage()
    if state.date != _today():
        state = UsageState(date=_today(), ai_calls=0)
    return state


def record_audit_log(entry: dict[str, Any]) -> None:
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True)
    AUDIT_PATH.write_text("", encoding="utf-8") if not AUDIT_PATH.exists() else None
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
