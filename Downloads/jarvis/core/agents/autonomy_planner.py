"""
Turns broad requests into small actionable plans.
"""

import json
import re
import threading
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


PLANS_FILE = Path("./memory_stack/autonomy_plans.json")


@dataclass
class PlanStep:
    step_id: str
    text: str
    status: str = "todo"  # todo | doing | done


@dataclass
class Plan:
    plan_id: str
    source_request: str
    steps: List[PlanStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class AutonomyPlanner:
    def __init__(self, llm_fn):
        self.llm = llm_fn
        self._lock = threading.Lock()
        self.plans: List[Plan] = self._load()

    def _load(self) -> List[Plan]:
        if PLANS_FILE.exists():
            try:
                raw = json.loads(PLANS_FILE.read_text())
                out = []
                for p in raw:
                    steps = [PlanStep(**s) for s in p.get("steps", []) if isinstance(s, dict)]
                    out.append(Plan(plan_id=p.get("plan_id", str(uuid.uuid4())[:8]), source_request=p.get("source_request", ""), steps=steps, created_at=p.get("created_at", datetime.now().isoformat())))
                return out
            except Exception:
                pass
        return []

    def _save(self):
        PLANS_FILE.parent.mkdir(exist_ok=True)
        data = []
        for p in self.plans:
            row = asdict(p)
            data.append(row)
        PLANS_FILE.write_text(json.dumps(data, indent=2))

    def create_plan(self, request_text: str) -> Plan:
        steps = self._llm_steps(request_text)
        if not steps:
            steps = self._heuristic_steps(request_text)

        plan = Plan(
            plan_id=str(uuid.uuid4())[:8],
            source_request=request_text,
            steps=[PlanStep(step_id=f"s{i+1}", text=s) for i, s in enumerate(steps)],
        )
        with self._lock:
            self.plans.append(plan)
            self._save()
        return plan

    def _llm_steps(self, request_text: str) -> List[str]:
        prompt = (
            "Convert this request into 3-6 concrete next steps. "
            "Return only a JSON array of strings.\n\n"
            f"Request: {request_text}"
        )
        try:
            result = self.llm(prompt, max_tokens=180, temperature=0.2)
            result = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
            data = json.loads(result)
            if isinstance(data, list):
                clean = [str(x).strip() for x in data if str(x).strip()]
                return clean[:6]
        except Exception:
            pass
        return []

    def _heuristic_steps(self, request_text: str) -> List[str]:
        base = request_text.strip().rstrip(".?!")
        return [
            f"Clarify the exact outcome for: {base}",
            "Break the work into smallest executable tasks",
            "Execute first task and capture result",
            "Review result and decide next step",
        ]

    def latest_plan_summary(self) -> str:
        with self._lock:
            if not self.plans:
                return "No plans created yet."
            p = self.plans[-1]
        lines = [f"Plan {p.plan_id}: {p.source_request}"]
        for s in p.steps[:8]:
            lines.append(f"- [{s.status}] {s.text}")
        return "\n".join(lines)
