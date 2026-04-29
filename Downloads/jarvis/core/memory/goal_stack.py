"""
Persistent goal stack that survives across sessions.
Goals feed the morning briefing and every system prompt.
L3 memory facts promote into goals when relevant.
"""

import json
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional

GOAL_FILE = Path("./memory_stack/goal_stack.json")


class GoalStack:
    def __init__(self, llm_fn):
        self.llm = llm_fn
        self._goals = self._load()

    def _load(self) -> list:
        if GOAL_FILE.exists():
            try:
                return json.loads(GOAL_FILE.read_text())
            except Exception:
                pass
        return []

    def _save(self):
        GOAL_FILE.parent.mkdir(exist_ok=True)
        GOAL_FILE.write_text(json.dumps(self._goals, indent=2))

    def push(self, title: str, deadline: str = "", priority: int = 5) -> dict:
        goal = {
            "id": f"g_{int(time.time())}",
            "title": title,
            "priority": priority,
            "deadline": deadline,
            "progress": 0.0,
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "notes": []
        }
        self._goals.insert(0, goal)
        self._save()
        return goal

    def update_progress(self, goal_id: str, progress: float, note: str = ""):
        for g in self._goals:
            if g["id"] == goal_id:
                g["progress"] = min(1.0, max(0.0, progress))
                g["updated_at"] = datetime.now().isoformat()
                if note:
                    g["notes"].append({"note": note, "ts": datetime.now().isoformat()})
                if g["progress"] >= 1.0:
                    g["status"] = "done"
                break
        self._save()

    def complete(self, goal_id: str):
        self.update_progress(goal_id, 1.0)

    def cancel(self, goal_id: str):
        for g in self._goals:
            if g["id"] == goal_id:
                g["status"] = "cancelled"
                g["updated_at"] = datetime.now().isoformat()
                break
        self._save()

    def active(self) -> List[dict]:
        active = [g for g in self._goals if g["status"] == "active"]
        return sorted(active, key=lambda g: (g["priority"], g["updated_at"]))

    def overdue(self) -> List[dict]:
        now = datetime.now().isoformat()
        result = []
        for g in self.active():
            if g["deadline"] and g["deadline"] < now:
                result.append(g)
        return result

    def infer_from_input(self, user_input: str) -> Optional[str]:
        goal_signals = [
            "i want to", "i need to", "i have to",
            "my goal is", "i'm trying to", "i plan to",
            "i should", "remind me to finish"
        ]
        t = user_input.lower()
        if any(sig in t for sig in goal_signals):
            prompt = (
                f"Extract the user's goal from: '{user_input}'\n"
                f"Return ONLY the goal title in 5 words or less. "
                f"No explanation. Example: 'finish JARVIS phase 19'"
            )
            title = self.llm(prompt, max_tokens=20, temperature=0.1)
            if title and len(title.split()) <= 8:
                self.push(title.strip())
                return title.strip()
        return None

    def for_prompt(self) -> str:
        active = self.active()[:5]
        if not active:
            return ""
        lines = ["Active goals:"]
        for g in active:
            deadline = f" (due: {g['deadline'][:10]})" if g["deadline"] else ""
            lines.append(f"  - {g['title']} [{g['progress']:.0%}]{deadline}")
        overdue = self.overdue()
        if overdue:
            lines.append(f"OVERDUE: {', '.join(g['title'] for g in overdue)}")
        return "\n".join(lines)

    def morning_briefing_entry(self) -> str:
        active = self.active()
        overdue = self.overdue()
        if not active:
            return "No active goals."
        top = active[:3]
        text = f"{len(active)} active goals. "
        text += "Top: " + ", ".join(f"{g['title']} ({g['progress']:.0%})" for g in top)
        if overdue:
            text += f" ⚠ {len(overdue)} overdue."
        return text

    def summary(self) -> str:
        active = len(self.active())
        done = len([g for g in self._goals if g["status"] == "done"])
        overdue = len(self.overdue())
        return f"Goal stack: {active} active | {done} completed | {overdue} overdue | {len(self._goals)} total"
