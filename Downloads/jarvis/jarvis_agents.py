"""
JARVIS Multi-Agent Architecture
JARVIS acts as orchestrator. Other agents handle specialized tasks.
All agents communicate via our HSL-enriched MCP protocol.
"""

import threading
import queue
import json
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class AgentTask:
    task_id:     str
    agent_name:  str
    description: str
    params:      dict
    priority:    int = 5  # 1=critical, 10=background
    callback:    Callable = None


class SpecializedAgent:
    """Base class for all JARVIS sub-agents."""
    def __init__(self, name: str, description: str):
        self.name        = name
        self.description = description
        self.task_queue  = queue.PriorityQueue()
        self.running     = False

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            try:
                priority, task = self.task_queue.get(timeout=1)
                result = self.execute(task)
                if task.callback:
                    task.callback(result)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Agent {self.name} error: {e}")

    def submit(self, task: AgentTask):
        self.task_queue.put((task.priority, task))

    def execute(self, task: AgentTask) -> str:
        raise NotImplementedError


class ResearchAgent(SpecializedAgent):
    """Autonomous research agent — runs multi-source research overnight."""
    def __init__(self, llm_fn, search_fn):
        super().__init__("ResearchAgent", "Multi-source autonomous research")
        self.llm    = llm_fn
        self.search = search_fn
        self.queue_: list = []

    def execute(self, task: AgentTask) -> str:
        topic      = task.params.get("topic", "")
        depth      = task.params.get("depth", "standard")
        n_sources  = 5 if depth == "deep" else 3

        print(f"ResearchAgent: researching '{topic}' at depth={depth}")

        # Multi-step research pipeline
        results = []

        # Step 1: Initial search
        initial = self.search(topic, n_sources)
        results.append(initial)

        # Step 2: Generate follow-up questions
        followup_prompt = (
            f"Based on initial research on '{topic}', generate 3 specific follow-up "
            f"questions that would deepen understanding. Return as JSON list."
        )
        followups_raw = self.llm(followup_prompt, max_tokens=200, temperature=0.3)
        try:
            followups = json.loads(followups_raw)
        except Exception:
            followups = []

        # Step 3: Research each follow-up
        for fq in followups[:2]:
            fq_result = self.search(fq, 2)
            results.append(f"Follow-up '{fq}':\n{fq_result}")

        # Step 4: Synthesize everything
        combined = "\n\n".join(results)
        synthesis_prompt = (
            f"Synthesize this research on '{topic}' into a comprehensive report. "
            f"Include: summary, key findings, implications, open questions.\n\n"
            f"{combined[:5000]}"
        )
        report = self.llm(synthesis_prompt, max_tokens=800, temperature=0.3)
        return report


class CodeAgent(SpecializedAgent):
    """Code analysis, generation, and debugging agent."""
    def __init__(self, llm_fn):
        super().__init__("CodeAgent", "Code analysis and generation")
        self.llm = llm_fn

    def execute(self, task: AgentTask) -> str:
        action  = task.params.get("action", "analyze")
        code    = task.params.get("code", "")
        context = task.params.get("context", "")

        if action == "analyze":
            prompt = f"Analyze this code. Find bugs, suggest improvements:\n{code}"
        elif action == "generate":
            prompt = f"Write Python code to: {context}. Return only code."
        elif action == "debug":
            error  = task.params.get("error", "")
            prompt = f"Debug this code. Error: {error}\n\nCode:\n{code}\n\nFix the issue."
        else:
            prompt = f"Code task: {context}\n\n{code}"

        return self.llm(prompt, max_tokens=1000, temperature=0.1)


class MemoryAgent(SpecializedAgent):
    """Manages memory synthesis and pattern detection."""
    def __init__(self, llm_fn, memory_ref, profile_ref):
        super().__init__("MemoryAgent", "Memory management and synthesis")
        self.llm     = llm_fn
        self.memory  = memory_ref
        self.profile = profile_ref

    def execute(self, task: AgentTask) -> str:
        action = task.params.get("action", "synthesize")
        if action == "synthesize":
            self.profile.synthesize_from_memory()
            return "Profile synthesized."
        elif action == "patterns":
            # Detect patterns in memory
            c = self.memory.count()
            if c < 10:
                return "Not enough memory for pattern analysis."
            r = self.memory.query(
                query_texts=["user habits patterns preferences"],
                n_results=min(30, c)
            )
            docs    = r["documents"][0]
            prompt  = f"Find 3 behavioral patterns in these interactions:\n" + "\n".join(docs[:20])
            return self.llm(prompt, max_tokens=200, temperature=0.3)
        return "Unknown memory action."


class JarvisOrchestrator:
    """
    JARVIS as multi-agent orchestrator.
    Routes tasks to specialized agents.
    Maintains HSL context across all agents.
    """
    def __init__(self, llm_fn, search_fn, memory_ref, profile_ref):
        self.agents = {
            "research": ResearchAgent(llm_fn, search_fn),
            "code":     CodeAgent(llm_fn),
            "memory":   MemoryAgent(llm_fn, memory_ref, profile_ref),
        }

    def start_all(self):
        for agent in self.agents.values():
            agent.start()
        print(f"JARVIS Orchestrator: {len(self.agents)} agents online.")

    def dispatch(self, agent_name: str, task: AgentTask) -> str:
        """Dispatch a task to a specific agent, or auto-route."""
        if agent_name == "auto":
            agent_name = self._route(task)

        agent = self.agents.get(agent_name)
        if not agent:
            return f"No agent named: {agent_name}"

        # For quick tasks, run synchronously
        if task.priority <= 3:
            return agent.execute(task)

        # For background tasks, submit asynchronously
        result_holder = []
        def callback(r):
            result_holder.append(r)
        task.callback = callback
        agent.submit(task)
        return f"Task submitted to {agent_name} agent."

    def _route(self, task: AgentTask) -> str:
        desc = task.description.lower()
        if any(w in desc for w in ["research", "study", "investigate", "learn"]):
            return "research"
        if any(w in desc for w in ["code", "debug", "program", "script", "function"]):
            return "code"
        if any(w in desc for w in ["memory", "remember", "pattern", "profile"]):
            return "memory"
        return "research"  # Default

    def schedule_overnight_research(self, topics: list):
        """Schedule research tasks to run in the background."""
        for i, topic in enumerate(topics):
            task = AgentTask(
                task_id    = f"research_{i}",
                agent_name = "research",
                description= f"Deep research on: {topic}",
                params     = {"topic": topic, "depth": "deep"},
                priority   = 8,  # Background
            )
            self.agents["research"].submit(task)
        print(f"Scheduled {len(topics)} overnight research tasks.")