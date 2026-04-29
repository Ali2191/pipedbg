"""
Tracks explicit user goals and progress over time.
"""

import json
import threading
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List


GOALS_FILE = Path("./memory_stack/goals.json")


@dataclass
class Goal:
    goal_id: str
    title: str
    status: str = "active"  # active | paused | done
    progress: int = 0
    mentions: int = 1
    last_mentioned: str = field(default_factory=lambda: datetime.now().isoformat())
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class GoalEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self.goals: List[Goal] = self._load()

    def _load(self) -> List[Goal]:
        if GOALS_FILE.exists():
            try:
                raw = json.loads(GOALS_FILE.read_text())
                return [Goal(**g) for g in raw if isinstance(g, dict) and g.get("goal_id")]
            except Exception:
                pass
        return []

    def _save(self):
        GOALS_FILE.parent.mkdir(exist_ok=True)
        GOALS_FILE.write_text(json.dumps([asdict(g) for g in self.goals], indent=2))

    def observe_input(self, user_input: str):
        t = user_input.lower().strip()

        goal_prefixes = [
            "my goal is", "goal:", "i want to", "i need to", "i plan to", "i will",
        ]
        done_markers = ["i finished", "i completed", "done with", "i shipped"]

        with self._lock:
            for marker in done_markers:
                if marker in t:
                    for g in self.goals:
                        if g.status == "active" and any(tok in t for tok in g.title.lower().split()[:3]):
                            g.status = "done"
                            g.progress = 100
                            g.last_mentioned = datetime.now().isoformat()
                    self._save()
                    return

            for p in goal_prefixes:
                if t.startswith(p):
                    title = user_input[len(p):].strip(" .,!?")
                    if title:
                        self._upsert_goal(title)
                    return

    def _upsert_goal(self, title: str):
        key = title.lower()
        for g in self.goals:
            if g.title.lower() == key:
                g.mentions += 1
                g.last_mentioned = datetime.now().isoformat()
                g.progress = min(95, g.progress + 5)
                self._save()
                return

        self.goals.append(Goal(goal_id=str(uuid.uuid4())[:8], title=title))
        self._save()

    def summary(self, top_n: int = 5) -> str:
        with self._lock:
            if not self.goals:
                return "No tracked goals yet. Say: 'my goal is ...'"
            ranked = sorted(self.goals, key=lambda g: (g.status != "done", -g.mentions, -g.progress))[:top_n]
        lines = [f"{g.goal_id} | {g.status} | {g.progress}% | {g.title}" for g in ranked]
        return "Goals:\n" + "\n".join(lines)

    def active_goal_context(self, top_n: int = 3) -> str:
        with self._lock:
            active = [g for g in self.goals if g.status == "active"]
            active = sorted(active, key=lambda g: (-g.mentions, -g.progress))[:top_n]
        if not active:
            return ""
        return "Active goals: " + "; ".join(f"{g.title} ({g.progress}%)" for g in active)
