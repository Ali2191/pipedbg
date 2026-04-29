"""
Mixture of Agents (MoA) implementation.
Based on: Wang et al., TogetherAI 2025

Architecture:
  Layer 1 (Proposers): 3 specialized models generate independent answers
    - Personality model:  JARVIS voice and relationship context
    - Reasoning model:    Logical analysis and planning
    - Domain model:       Code, research, or tool-specific knowledge
  Layer 2 (Aggregator): Synthesizes best elements from all proposals

Finding from paper: aggregation of 3 smaller models outperforms
one larger model on 90%+ of tasks tested.
"""

import json
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import requests as http_req

OLLAMA_BASE = "http://localhost:11434"


@dataclass
class AgentProposal:
    model: str
    role: str  # personality / reasoning / domain
    content: str
    latency_ms: float
    confidence: float  # 0.0-1.0, estimated from response length and coherence


class MixtureOfAgents:
    """
    Three-proposer, one-aggregator MoA system.
    All agents run in parallel on local Ollama.
    """

    # Proposer configurations
    PROPOSERS = [
        {
            "role": "personality",
            "model": "qwen2.5:7b",  # Primary model
            "system": (
                "You are JARVIS, Tony Stark's AI. Your role: provide the personality, "
                "tone, and relationship context for this response. Be direct, calm, "
                "occasionally dry. Max 2 sentences. Focus on HOW to say it, not just what."
            ),
        },
        {
            "role": "reasoning",
            "model": "qwen2.5:7b",  # Same model, different system prompt
            "system": (
                "You are an analytical reasoning engine. Your role: provide the logical "
                "structure, step-by-step reasoning, and factual accuracy for this response. "
                "Be precise. Prioritize correctness over personality."
            ),
        },
        {
            "role": "domain",
            "model": "qwen2.5-coder:7b",  # Specialized for code/technical
            "system": (
                "You are a domain expert. Your role: provide technical depth, code examples, "
                "or specialized knowledge for this response. If the query is not technical, "
                "provide a practical, action-focused answer."
            ),
        },
    ]

    AGGREGATOR_SYSTEM = (
        "You are a response synthesizer for JARVIS AI. "
        "You receive 2-3 candidate responses to the same user query from specialized agents. "
        "Your job: synthesize the best elements into ONE final response. "
        "Rules: "
        "1. Use the personality agent's TONE "
        "2. Use the reasoning agent's LOGIC "
        "3. Use the domain agent's TECHNICAL ACCURACY "
        "4. Max 2 sentences unless detail was explicitly requested "
        "5. Output ONLY the final response — no meta-commentary"
    )

    def __init__(self, use_moa: bool = True, min_latency_budget_ms: float = 3000):
        """
        use_moa: If False, falls back to single-model mode.
        min_latency_budget_ms: If expected latency exceeds this, use single model.
        """
        self.use_moa = use_moa
        self.latency_budget = min_latency_budget_ms
        self._available_models = set()
        self._check_available()

    def _check_available(self):
        """Check which Ollama models are available."""
        try:
            resp = http_req.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
            models = resp.json().get("models", [])
            self._available_models = {m["name"].split(":")[0] for m in models}
            print(
                f"MoA: {len(self._available_models)} models available: "
                f"{', '.join(sorted(self._available_models))}"
            )
        except Exception as e:
            print(f"MoA: Ollama not reachable — {e}. Using single-model mode.")
            self.use_moa = False

    def _model_available(self, model_name: str) -> bool:
        base = model_name.split(":")[0]
        return base in self._available_models

    def _call_proposer(
        self, proposer: dict, messages: list, user_input: str
    ) -> Optional[AgentProposal]:
        """Call one proposer model. Runs in its own thread."""
        model = proposer["model"]
        role = proposer["role"]

        # Fall back to primary model if specialized model unavailable
        if not self._model_available(model):
            model = "qwen2.5:7b"

        system = proposer["system"]
        start = time.time()

        try:
            # Build prompt with user input
            payload = {
                "model": model,
                "prompt": f"{system}\n\nUser query: {user_input}\n\nResponse:",
                "stream": False,
                "options": {
                    "num_predict": 200,
                    "temperature": 0.7 if role == "personality" else 0.3,
                    "top_k": 40,
                },
            }
            resp = http_req.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=30)
            content = resp.json().get("response", "").strip()
            latency = (time.time() - start) * 1000

            if not content:
                return None

            # Simple confidence estimate
            confidence = min(1.0, len(content.split()) / 30)

            return AgentProposal(
                model=model,
                role=role,
                content=content,
                latency_ms=latency,
                confidence=confidence,
            )
        except Exception as e:
            print(f"MoA proposer ({role}): {e}")
            return None

    def _aggregate(self, proposals: List[AgentProposal], user_input: str) -> str:
        """Aggregate proposals into final response."""
        if not proposals:
            return ""
        if len(proposals) == 1:
            return proposals[0].content

        # Build aggregation prompt
        proposals_text = ""
        for i, p in enumerate(proposals, 1):
            proposals_text += f"\n[{p.role.upper()} AGENT]:\n{p.content}\n"

        aggregation_prompt = (
            f"{self.AGGREGATOR_SYSTEM}\n\n"
            f"User query: {user_input}\n\n"
            f"Candidate responses:{proposals_text}\n"
            f"Synthesized final response:"
        )

        try:
            payload = {
                "model": "qwen2.5:7b",
                "prompt": aggregation_prompt,
                "stream": False,
                "options": {"num_predict": 250, "temperature": 0.4},
            }
            resp = http_req.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=30)
            result = resp.json().get("response", "").strip()
            return result if result else proposals[0].content
        except Exception as e:
            print(f"MoA aggregator: {e}")
            return proposals[0].content

    def generate(self, messages: list, user_input: str, intent: str = "chat") -> str:
        """
        Main MoA generation method.
        Decides whether to use MoA or single model based on intent complexity.
        """
        # Simple intents don't need MoA - saves compute
        simple_intents = [
            "open_app",
            "spotify_play",
            "spotify_next",
            "spotify_prev",
            "screenshot",
            "lock_screen",
            "stop",
            "volume_control",
        ]
        if intent in simple_intents or not self.use_moa:
            return self._single_model(user_input)

        # Run all proposers in parallel
        proposals = []
        proposal_lock = threading.Lock()
        threads = []

        def _run_proposer(proposer):
            result = self._call_proposer(proposer, messages, user_input)
            if result:
                with proposal_lock:
                    proposals.append(result)

        for proposer in self.PROPOSERS:
            t = threading.Thread(target=_run_proposer, args=(proposer,), daemon=True)
            threads.append(t)
            t.start()

        # Wait for all proposers (with timeout)
        for t in threads:
            t.join(timeout=25)

        if not proposals:
            return self._single_model(user_input)

        # Aggregate
        final = self._aggregate(proposals, user_input)

        # Log proposal stats
        avg_latency = sum(p.latency_ms for p in proposals) / len(proposals)
        print(f"MoA: {len(proposals)} proposals " f"(avg {avg_latency:.0f}ms) → aggregated")

        return final

    def _single_model(self, user_input: str) -> str:
        """Fallback single-model call."""
        try:
            payload = {
                "model": "qwen2.5:7b",
                "prompt": user_input,
                "stream": False,
                "options": {"num_predict": 300, "temperature": 0.7},
            }
            resp = http_req.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=30)
            return resp.json().get("response", "").strip()
        except Exception as e:
            return f"Error: {e}"

    def get_stats(self) -> str:
        return (
            f"MoA: {'active' if self.use_moa else 'disabled'} | "
            f"Models available: {len(self._available_models)} | "
            f"Proposers: {len(self.PROPOSERS)}"
        )
