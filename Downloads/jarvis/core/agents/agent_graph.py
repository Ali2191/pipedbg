"""
Autonomous agent graph with message-passing coordination.
JARVIS spawns task-specific sub-agents dynamically.
Each agent has its own HSL slice.
Coordination via lightweight message bus.
"""

import threading
import queue
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from datetime import datetime


@dataclass
class AgentMessage:
    msg_id:    str
    sender:    str
    recipient: str    # agent name or "broadcast"
    content:   str
    msg_type:  str    # task / result / status / cancel
    priority:  int    = 5
    timestamp: str    = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AgentHSLSlice:
    """Each agent gets its own slice of the HumanStateLayer."""
    agent_name:    str
    current_task:  str    = ""
    emotional_ctx: str    = "neutral"
    screen_ctx:    str    = ""
    memory_ctx:    str    = ""


class BaseAgent:
    """Base class for all graph agents."""

    def __init__(self, name: str, llm_fn: Callable,
                 message_bus: "MessageBus"):
        self.name        = name
        self.llm         = llm_fn
        self.bus         = message_bus
        self.hsl_slice   = AgentHSLSlice(agent_name=name)
        self._task_queue = queue.PriorityQueue()
        self._running    = False
        self._results    = {}  # task_id -> result

    def start(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()
        self.bus.register(self.name, self)
        print(f"Agent online: {self.name}")

    def _run(self):
        while self._running:
            try:
                priority, msg = self._task_queue.get(timeout=1)
                self.hsl_slice.current_task = msg.content[:50]
                result = self.handle(msg)
                if result:
                    self.bus.send(AgentMessage(
                        msg_id    = str(uuid.uuid4()),
                        sender    = self.name,
                        recipient = msg.sender,
                        content   = result,
                        msg_type  = "result"
                    ))
            except queue.Empty:
                continue
            except Exception as e:
                print(f"{self.name} error: {e}")

    def receive(self, msg: AgentMessage):
        self._task_queue.put((msg.priority, msg))

    def handle(self, msg: AgentMessage) -> Optional[str]:
        raise NotImplementedError


class ResearchAgent(BaseAgent):
    def handle(self, msg: AgentMessage) -> Optional[str]:
        topic  = msg.content
        result = self.llm(
            f"Research this topic and give a 3-paragraph summary: {topic}",
            max_tokens=500, temperature=0.3
        )
        return result


class CodeAgent(BaseAgent):
    def handle(self, msg: AgentMessage) -> Optional[str]:
        task   = msg.content
        result = self.llm(
            f"You are a senior engineer. {task}\nProvide complete, working code.",
            max_tokens=800, temperature=0.1
        )
        return result


class CalendarAgent(BaseAgent):
    def handle(self, msg: AgentMessage) -> Optional[str]:
        import subprocess
        script = 'tell application "Calendar" to return ' \
                 'every event of today of calendar 1'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return result or "No events today."


class SummaryAgent(BaseAgent):
    """Aggregator agent — synthesizes results from other agents."""
    def handle(self, msg: AgentMessage) -> Optional[str]:
        result = self.llm(
            f"Synthesize this information into a clear, actionable summary:"
            f"\n{msg.content}",
            max_tokens=200, temperature=0.4
        )
        return result


class MessageBus:
    """Lightweight message bus for agent-to-agent communication."""

    def __init__(self):
        self._agents:    Dict[str, BaseAgent] = {}
        self._inbox:     Dict[str, List]      = {}
        self._lock       = threading.Lock()

    def register(self, name: str, agent: BaseAgent):
        with self._lock:
            self._agents[name]  = agent
            self._inbox[name]   = []

    def send(self, msg: AgentMessage):
        with self._lock:
            if msg.recipient == "broadcast":
                for name, agent in self._agents.items():
                    if name != msg.sender:
                        agent.receive(msg)
            elif msg.recipient in self._agents:
                self._agents[msg.recipient].receive(msg)

    def agents_status(self) -> str:
        with self._lock:
            return " | ".join(
                f"{name}: {agent.hsl_slice.current_task[:30] or 'idle'}"
                for name, agent in self._agents.items()
            )


class AgentOrchestrator:
    """
    JARVIS as orchestrator of the agent graph.
    Receives high-level tasks, routes to appropriate agent(s),
    waits for results (async for background, sync for urgent).
    """

    def __init__(self, llm_fn: Callable):
        self.llm  = llm_fn
        self.bus  = MessageBus()
        self._results_queue = queue.Queue()

        # Spawn all agents
        self.agents = {
            "research":  ResearchAgent("research",  llm_fn, self.bus),
            "code":      CodeAgent("code",           llm_fn, self.bus),
            "calendar":  CalendarAgent("calendar",   llm_fn, self.bus),
            "summary":   SummaryAgent("summary",     llm_fn, self.bus),
        }

    def start(self):
        for agent in self.agents.values():
            agent.start()
        print(f"Agent graph online: {len(self.agents)} agents")

    def dispatch(self, task: str, agent_name: str = "auto",
                 wait_secs: float = 0) -> Optional[str]:
        """
        Dispatch a task to the agent graph.
        wait_secs=0: fire and forget (background)
        wait_secs>0: wait up to N seconds for result
        """
        if agent_name == "auto":
            agent_name = self._route(task)

        msg = AgentMessage(
            msg_id    = str(uuid.uuid4()),
            sender    = "jarvis_orchestrator",
            recipient = agent_name,
            content   = task,
            msg_type  = "task",
            priority  = 3 if wait_secs > 0 else 7
        )
        self.bus.send(msg)

        if wait_secs > 0:
            # Poll for result
            target = self.agents.get(agent_name)
            if target:
                deadline = time.time() + wait_secs
                while time.time() < deadline:
                    if target._results:
                        return list(target._results.values())[-1]
                    time.sleep(0.2)
        return f"Task dispatched to {agent_name} agent."

    def _route(self, task: str) -> str:
        t = task.lower()
        if any(w in t for w in ["research", "study", "investigate", "learn"]):
            return "research"
        if any(w in t for w in ["code", "function", "bug", "debug", "program"]):
            return "code"
        if any(w in t for w in ["calendar", "schedule", "event", "meeting"]):
            return "calendar"
        return "research"  # default

    def status(self) -> str:
        return f"Agent graph: {self.bus.agents_status()}"
