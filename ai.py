"""
pipedbg.ai
===========
AI failure explainer and patch application helpers.

The AI path is intentionally structured around JSON so the CLI can safely apply
small YAML changes in-place with a backup copy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import anthropic  # type: ignore
except Exception:  # pragma: no cover - optional dependency in env
    anthropic = None

from .runner import StepResult


@dataclass
class AIResponse:
    explanation: str
    suggestion: str = ""
    changes: list[dict[str, str]] = None

    def __post_init__(self) -> None:
        if self.changes is None:
            self.changes = []


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract and decode the first JSON object found in a model response."""
    text = text.strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    snippet = text[start : end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return {}


def _response_text(response: Any) -> str:
    if hasattr(response, "completion"):
        return str(response.completion)
    if hasattr(response, "content"):
        parts = []
        for item in response.content:
            parts.append(getattr(item, "text", str(item)))
        return "".join(parts)
    return str(response)


def _build_prompt(step_result: StepResult) -> str:
    logs = "\n".join(step_result.logs[-200:])
    return (
        "You are a CI debugging assistant. Return ONLY valid JSON.\n"
        "Schema:\n"
        "{\n"
        '  "explanation": "short plain-English root cause",\n'
        '  "suggestion": "short human-readable fix summary",\n'
        '  "changes": [\n'
        '    {"find": "exact text to replace", "replace": "exact replacement text"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Prefer one or two minimal find/replace changes.\n"
        "- The `find` text must appear verbatim in the workflow file.\n"
        "- The `replace` text should be the corrected YAML snippet.\n"
        "- If no YAML fix is appropriate, return an empty `changes` array.\n\n"
        f"Step: {step_result.step.display_name()}\n"
        f"Step ID: {step_result.step.id}\n"
        f"Exit code: {step_result.exit_code}\n"
        "Logs:\n"
        f"{logs}\n"
    )


def _call_anthropic(prompt: str, max_tokens: int) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not (anthropic and api_key):
        return ""

    client = None
    if hasattr(anthropic, "Anthropic"):
        client = anthropic.Anthropic(api_key=api_key)
    elif hasattr(anthropic, "Client"):
        client = anthropic.Client(api_key=api_key)
    else:
        return ""

    # Support both the newer messages API and the older completions API.
    if hasattr(client, "messages"):
        response = client.messages.create(
            model=os.environ.get("PIPEDBG_AI_MODEL", "claude-3-5-sonnet-20241022"),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return _response_text(response)

    if hasattr(client, "completions"):
        response = client.completions.create(
            model=os.environ.get("PIPEDBG_AI_MODEL", "claude-2.1"),
            prompt=prompt,
            max_tokens_to_sample=max_tokens,
        )
        return _response_text(response)

    return ""


def explain_failure(step_result: StepResult, max_tokens: int = 512) -> dict[str, Any]:
    """Return explanation + fix proposal as structured JSON-compatible data."""
    prompt = _build_prompt(step_result)
    raw_text = _call_anthropic(prompt, max_tokens=max_tokens)

    if raw_text:
        parsed = _extract_json_object(raw_text)
        if parsed:
            parsed.setdefault("changes", [])
            parsed.setdefault("suggestion", "")
            parsed.setdefault("explanation", raw_text)
            return parsed

        return {
            "explanation": raw_text,
            "suggestion": "",
            "changes": [],
        }

    # Safe fallback for local/demo mode.
    logs_preview = "\n".join(step_result.logs[-10:])
    return {
        "explanation": (
            f"The step failed with exit code {step_result.exit_code}. The last log lines suggest a "
            "missing executable or misconfiguration."
        ),
        "suggestion": (
            "Suggested quick checks: ensure the command exists in the runner image and reproduce the "
            "command in the same container locally."
        ),
        "changes": [],
        "logs_preview": logs_preview,
    }


def apply_yaml_changes(workflow_path: str | Path, changes: list[dict[str, str]]) -> Path:
    """Apply exact string replacements to a workflow file with a backup copy.

    Returns the backup path. Raises ValueError if a replacement is ambiguous.
    """
    path = Path(workflow_path)
    original = path.read_text(encoding="utf-8")
    updated = original

    for change in changes:
        find = change.get("find", "")
        replace = change.get("replace", "")
        if not find:
            raise ValueError("AI change is missing `find` text.")
        count = updated.count(find)
        if count != 1:
            raise ValueError(
                f"Cannot apply change for `{find}` because it matched {count} times; "
                "please refine the AI suggestion."
            )
        updated = updated.replace(find, replace, 1)

    backup_path = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup_path)
    path.write_text(updated, encoding="utf-8")
    return backup_path

