"""
Hierarchical Task Network planner with LLM decomposition.
Based on: HTN + LLM integration papers, ICLR 2025.

Architecture:
  Root goal -> HTN decomposer -> subtask tree
  Each subtask -> verified executable? -> execute
  Failed subtask -> replanned
  Progress tracked per node

Example:
  Goal: "Plan my next month"
  -> L1: Career tasks, Personal goals, Health habits
  -> L2: Career: finish Phase 19, push to GitHub, write docs
  -> L3: finish Phase 19: write htn_planner.py, test, commit
"""

import json
import re
import time
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from pathlib import Path
from datetime import datetime

HTN_FILE = Path("./memory_stack/htn_goals.json")


@dataclass
class GoalNode:
    node_id: str
    title: str
    description: str
    level: int
    parent_id: Optional[str]
    children: List[str] = field(default_factory=list)
    status: str = "pending"
    progress: float = 0.0
    deadline: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    is_atomic: bool = False
    execute_cmd: str = ""


class HTNPlanner:
    """Hierarchical Task Network planner with persistent goal tree."""

    def __init__(self, llm_fn, execute_fn):
        self.llm = llm_fn
        self.execute = execute_fn
        self._nodes: Dict[str, GoalNode] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if HTN_FILE.exists():
            try:
                data = json.loads(HTN_FILE.read_text())
                for nid, nd in data.items():
                    self._nodes[nid] = GoalNode(**nd)
                print(f"HTN: {len(self._nodes)} goal nodes loaded")
            except Exception as e:
                print(f"HTN load: {e}")

    def _save(self):
        HTN_FILE.parent.mkdir(exist_ok=True)
        HTN_FILE.write_text(json.dumps({nid: {k: v for k, v in nd.__dict__.items()} for nid, nd in self._nodes.items()}, indent=2))

    def add_goal(self, title: str, description: str = "", deadline: str = "", auto_decompose: bool = True) -> str:
        node_id = f"goal_{int(time.time())}"
        root = GoalNode(node_id=node_id, title=title, description=description or title, level=0, parent_id=None, deadline=deadline, status="active")
        with self._lock:
            self._nodes[node_id] = root

        if auto_decompose:
            threading.Thread(target=self._decompose, args=(root,), daemon=True).start()

        self._save()
        return node_id

    def _decompose(self, node: GoalNode, depth: int = 0):
        if depth > 3 or node.is_atomic:
            return

        prompt = (
            f"Decompose this goal into 3-5 concrete subtasks.\n"
            f"Goal: {node.title}\n"
            f"Description: {node.description}\n\n"
            f"For each subtask, specify if it's directly executable (atomic).\n"
            f"Return JSON list: "
            f'[{{"title": str, "description": str, "atomic": bool, "execute_cmd": str}}]\n'
            f"execute_cmd: the exact JARVIS command to run for atomic tasks (or empty)"
        )
        try:
            result = self.llm(prompt, max_tokens=400, temperature=0.2)
            result = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
            subtasks = json.loads(result)

            with self._lock:
                for st in subtasks:
                    child_id = f"goal_{int(time.time() * 1000)}"
                    child = GoalNode(
                        node_id=child_id,
                        title=st.get("title", ""),
                        description=st.get("description", ""),
                        level=node.level + 1,
                        parent_id=node.node_id,
                        is_atomic=st.get("atomic", False),
                        execute_cmd=st.get("execute_cmd", ""),
                        status="pending"
                    )
                    self._nodes[child_id] = child
                    node.children.append(child_id)
                    if not child.is_atomic and node.level < 2:
                        self._decompose(child, depth + 1)

            self._save()
            print(f"HTN: decomposed '{node.title}' into {len(subtasks)} subtasks")
        except Exception as e:
            print(f"HTN decompose error: {e}")

    def mark_done(self, node_id: str):
        with self._lock:
            node = self._nodes.get(node_id)
            if node:
                node.status = "done"
                node.progress = 1.0
                node.completed_at = datetime.now().isoformat()
                if node.parent_id and node.parent_id in self._nodes:
                    parent = self._nodes[node.parent_id]
                    siblings = [self._nodes[c] for c in parent.children if c in self._nodes]
                    done = sum(1 for s in siblings if s.status == "done")
                    parent.progress = done / len(siblings) if siblings else 0
        self._save()

    def get_active_goals(self, max_n: int = 5) -> List[GoalNode]:
        with self._lock:
            roots = [n for n in self._nodes.values() if n.level == 0 and n.status == "active"]
        return sorted(roots, key=lambda n: n.created_at, reverse=True)[:max_n]

    def get_next_actions(self) -> List[GoalNode]:
        with self._lock:
            return [n for n in self._nodes.values() if n.is_atomic and n.status == "pending" and n.execute_cmd][:5]

    def get_goal_summary(self) -> str:
        active = self.get_active_goals()
        if not active:
            return "No active goals in goal stack."
        lines = ["**Active Goals:**"]
        for goal in active:
            with self._lock:
                children = [self._nodes[c] for c in goal.children if c in self._nodes and self._nodes[c].status == "pending"]
            next_action = children[0].title if children else "(completed)"
            lines.append(f"- {goal.title} [{goal.progress:.0%}]: next: {next_action}")
        return "\n".join(lines)

    def get_goal_tree(self, root_id: str, indent: int = 0) -> str:
        with self._lock:
            node = self._nodes.get(root_id)
        if not node:
            return ""
        status_icon = {
            "pending": "○", "active": "◉", "done": "✅", "failed": "❌", "cancelled": "⏹"
        }.get(node.status, "?")
        lines = ["  " * indent + f"{status_icon} {node.title} [{node.progress:.0%}]"]
        for child_id in node.children:
            child_tree = self.get_goal_tree(child_id, indent + 1)
            if child_tree:
                lines.append(child_tree)
        return "\n".join(lines)

    def inject_into_prompt(self) -> str:
        summary = self.get_goal_summary()
        actions = self.get_next_actions()
        if not actions:
            return summary
        action_list = ", ".join(a.title for a in actions[:3])
        return f"{summary}\n**Next executable actions:** {action_list}"
