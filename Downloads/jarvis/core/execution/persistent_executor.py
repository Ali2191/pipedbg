"""
Persistent execution threads — sleeping agents architecture.
JARVIS starts a multi-hour task, laptop closes, reopens, continues.
Task state persisted externally. Session boundary becomes invisible.
"""

import json
import time
import threading
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Callable
from enum import Enum

THREAD_DIR = Path("./execution_threads")
THREAD_DIR.mkdir(exist_ok=True)


class ThreadStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SLEEPING  = "sleeping"  # Waiting for session resume
    PAUSED    = "paused"    # Waiting for user input
    DONE      = "done"
    FAILED    = "failed"


@dataclass
class ExecutionStep:
    step_id:    int
    command:    str
    status:     str      = "pending"  # pending/done/failed/skipped
    result:     str      = ""
    started_at: str      = ""
    done_at:    str      = ""
    retries:    int      = 0
    notes:      str      = ""


@dataclass
class ExecutionThread:
    """
    A persistent execution unit that survives session boundaries.
    Serialized to disk whenever state changes.
    Resumed automatically on JARVIS startup.
    """
    thread_id:       str
    title:           str
    goal:            str
    steps:           List[ExecutionStep] = field(default_factory=list)
    current_step:    int                 = 0
    status:          str                 = "pending"
    created_at:      str                 = field(default_factory=lambda: datetime.now().isoformat())
    last_active:     str                 = ""
    session_count:   int                 = 0   # How many sessions this has run across
    context_snapshot: dict               = field(default_factory=dict)
    completion_pct:  float               = 0.0
    requires_input:  bool                = False
    waiting_for:     str                 = ""   # What input is needed


class PersistentExecutor:
    """
    Manages execution threads that persist across sessions.
    On startup: resumes all SLEEPING threads.
    On shutdown: serializes all RUNNING threads to SLEEPING.
    """

    def __init__(self, execute_fn: Callable, speak_fn: Callable,
                 llm_fn: Callable):
        self.execute    = execute_fn
        self.speak      = speak_fn
        self.llm        = llm_fn
        self._threads:  Dict[str, ExecutionThread] = {}
        self._lock      = threading.Lock()
        self._running   = True
        self._load_all()

    def _load_all(self):
        """Load all persisted threads on startup."""
        count = 0
        for f in THREAD_DIR.glob("*.json"):
            try:
                data   = json.loads(f.read_text())
                steps  = [ExecutionStep(**s) for s in data.pop("steps", [])]
                thread = ExecutionThread(**data)
                thread.steps = steps
                self._threads[thread.thread_id] = thread
                count += 1
            except Exception as e:
                print(f"Thread load error ({f}): {e}")
        if count:
            print(f"Persistent executor: {count} thread(s) loaded")

    def _save(self, thread: ExecutionThread):
        """Serialize thread to disk."""
        path = THREAD_DIR / f"{thread.thread_id}.json"
        data = asdict(thread)
        path.write_text(json.dumps(data, indent=2))

    def create_thread(self, title: str, goal: str,
                       steps: List[str]) -> ExecutionThread:
        """Create a new persistent execution thread."""
        thread_id = f"t_{hashlib.md5(goal.encode()).hexdigest()[:8]}_{int(time.time())}"
        thread = ExecutionThread(
            thread_id = thread_id,
            title     = title,
            goal      = goal,
            steps     = [ExecutionStep(step_id=i, command=cmd)
                         for i, cmd in enumerate(steps)],
            status    = "pending"
        )
        with self._lock:
            self._threads[thread_id] = thread
        self._save(thread)
        return thread

    def parse_and_create(self, user_input: str) -> Optional[ExecutionThread]:
        """
        Parse a complex request into a persistent execution thread.
        Used for long-horizon tasks that span multiple sessions.
        """
        long_horizon_signals = [
            "over the next", "by tomorrow", "by end of week",
            "multi-step", "plan for", "series of tasks",
            "when i wake up", "overnight", "while i sleep",
            "schedule for", "automate"
        ]
        if not any(sig in user_input.lower() for sig in long_horizon_signals):
            return None

        # Decompose into steps
        prompt = (
            f"Break this long-horizon task into 5-10 sequential steps:\n"
            f"Task: {user_input}\n\n"
            f"Each step must be a concrete JARVIS command.\n"
            f"Return JSON: {{\"title\": str, \"steps\": [\"step1\", \"step2\"]}}"
        )
        try:
            result = self.llm(prompt, max_tokens=400, temperature=0.2)
            result = result.replace("```json", "").replace("```", "").strip()
            data   = json.loads(result)
            thread = self.create_thread(
                title = data["title"],
                goal  = user_input,
                steps = data["steps"]
            )
            return thread
        except Exception as e:
            print(f"Thread parse error: {e}")
            return None

    def resume_sleeping_threads(self):
        """Called on JARVIS startup — resume all sleeping threads."""
        with self._lock:
            sleeping = [
                t for t in self._threads.values()
                if t.status == "sleeping"
            ]
        if sleeping:
            self.speak(
                f"Welcome back. I have {len(sleeping)} task(s) "
                f"that were running when you left. Resuming now."
            )
        for thread in sleeping:
            self._resume(thread)

    def _resume(self, thread: ExecutionThread):
        """Resume a sleeping thread."""
        thread.status        = "running"
        thread.session_count += 1
        thread.last_active   = datetime.now().isoformat()
        self._save(thread)
        threading.Thread(
            target=self._run_thread,
            args=(thread,), daemon=True
        ).start()
        print(f"Thread resumed: {thread.title} "
              f"(session #{thread.session_count})")

    def _run_thread(self, thread: ExecutionThread):
        """Execute steps sequentially, persisting state after each."""
        for i, step in enumerate(thread.steps):
            if step.status == "done":
                continue  # Already completed in previous session
            if i < thread.current_step:
                continue

            thread.current_step = i
            step.status         = "running"
            step.started_at     = datetime.now().isoformat()
            self._save(thread)

            self.speak(f"Step {i+1} of {len(thread.steps)}: {step.command[:60]}")

            try:
                result      = self.execute(step.command)
                step.result = result or "Done."
                step.status = "done"
                step.done_at = datetime.now().isoformat()
            except Exception as e:
                step.status = "failed"
                step.notes  = str(e)
                self.speak(f"Step {i+1} failed: {str(e)[:80]}. Continuing.")

            thread.completion_pct = sum(
                1 for s in thread.steps if s.status == "done"
            ) / len(thread.steps)
            self._save(thread)
            time.sleep(0.5)

        thread.status = "done"
        self._save(thread)
        self.speak(f"Task complete: {thread.title}")

    def sleep_all(self):
        """Called on JARVIS shutdown — mark running threads as sleeping."""
        with self._lock:
            running = [
                t for t in self._threads.values()
                if t.status == "running"
            ]
        for thread in running:
            thread.status      = "sleeping"
            thread.last_active = datetime.now().isoformat()
            self._save(thread)
            print(f"Thread sleeping: {thread.title} "
                  f"(step {thread.current_step}/{len(thread.steps)})")

    def get_active_summary(self) -> str:
        with self._lock:
            active = [t for t in self._threads.values()
                      if t.status in ["running", "sleeping", "pending"]]
        if not active:
            return "No active execution threads."
        lines = ["Active threads:"]
        for t in active:
            lines.append(
                f"  [{t.status}] {t.title}: "
                f"{t.completion_pct:.0%} complete "
                f"(session #{t.session_count})"
            )
        return "\n".join(lines)
