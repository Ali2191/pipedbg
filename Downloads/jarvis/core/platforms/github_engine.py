"""
core/github_engine.py
GitHub issue and git push helpers for JARVIS.
"""

import os
import subprocess
from pathlib import Path

import requests


def create_issue(repo: str, title: str, body: str = "", labels=None) -> str:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        return "GitHub error: GITHUB_TOKEN is not set."
    labels = labels or []
    response = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body, "labels": labels},
        timeout=30,
    )
    if response.ok:
        data = response.json()
        return f"Issue created: {data.get('html_url', '')}"
    return f"GitHub error: {response.status_code} {response.text[:200]}"


def push_repo(repo_path: str = ".", message: str = "JARVIS update", remote: str = "origin", branch: str = "") -> str:
    path = Path(repo_path).resolve()
    if not (path / ".git").exists():
        return f"GitHub error: no git repository at {path}"
    branch_arg = [branch] if branch else []
    steps = [
        ["git", "-C", str(path), "status", "--short"],
        ["git", "-C", str(path), "add", "-A"],
        ["git", "-C", str(path), "commit", "-m", message],
        ["git", "-C", str(path), "push", remote, *branch_arg],
    ]
    outputs = []
    for cmd in steps:
        result = subprocess.run(cmd, capture_output=True, text=True)
        outputs.append((cmd[2], result.returncode, result.stdout.strip(), result.stderr.strip()))
        if result.returncode != 0 and cmd[3] != "status":
            return f"GitHub error: {result.stderr.strip() or result.stdout.strip()}"
    return "Git push completed."