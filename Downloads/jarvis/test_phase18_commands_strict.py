#!/usr/bin/env python3
"""
Strict Phase 18 command tests.
Covers routing + process_request behavior for all new commands listed by user.
"""

import sys
from types import SimpleNamespace

sys.path.insert(0, "/Users/tayyab/Projects/jarvis")

import jarvis
from core.orchestration.compute_budget import ComputeBudget


class MockMoA:
    def __init__(self):
        self.calls = []

    def generate(self, conversation_history, user_input, intent="chat"):
        self.calls.append({"intent": intent, "user_input": user_input})
        return f"MOA_REPLY({intent})"

    def get_stats(self):
        return "proposers=3 | available=3 | avg=1200ms"


class MockConstitution:
    def __init__(self):
        self.filter_calls = 0

    def filter(self, response, user_input, context=""):
        self.filter_calls += 1
        return response, False

    def get_stats(self):
        return f"checks={self.filter_calls} corrections=0"


class MockTitan:
    def get_high_surprise_memories(self, threshold=0.6, n=5):
        return ["Unexpected SSH login at 02:30", "Rare crash after update"]

    def get_stats(self):
        return "Titan Memory: 42 long-term memories"

    def retrieve(self, query, n=3, surprise_weight=0.4):
        return ["Surprising memory snippet"]

    def store(self, content, context=""):
        return None


class MockAgentOrchestrator:
    def __init__(self):
        self.dispatched = []

    def status(self):
        return "research: idle | code: idle | calendar: idle | summary: idle"

    def dispatch(self, task, agent_name="auto", wait_secs=0):
        self.dispatched.append(
            {"task": task, "agent_name": agent_name, "wait_secs": wait_secs}
        )
        return f"Task dispatched to {agent_name} agent."


class MockCrossModal:
    def __init__(self):
        self.updates = []

    def as_hsl_injection(self):
        return "[CROSS-MODAL] Screen: VS Code | Mood: focused | Calendar: Standup 10:00"

    def update(self, modality, content, confidence=0.8):
        self.updates.append((modality, content, confidence))


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run():
    print("=" * 72)
    print("STRICT PHASE 18 COMMAND TEST")
    print("=" * 72)

    # Save originals to restore after tests.
    originals = {
        "moa": jarvis.moa,
        "constitution": jarvis.constitution,
        "titan": jarvis.titan,
        "agent_orchestrator": jarvis.agent_orchestrator,
        "cross_modal": jarvis.cross_modal,
        "budget": jarvis.budget,
        "llm_stream_and_speak": jarvis.llm_stream_and_speak,
        "deep_research": jarvis.deep_research,
        "get_calendar_events": jarvis.get_calendar_events,
        "world": jarvis.world,
        "conversation_history": list(jarvis.conversation_history),
    }

    try:
        # Install deterministic mocks.
        mock_moa = MockMoA()
        mock_constitution = MockConstitution()
        mock_titan = MockTitan()
        mock_agents = MockAgentOrchestrator()
        mock_cross_modal = MockCrossModal()

        jarvis.moa = mock_moa
        jarvis.constitution = mock_constitution
        jarvis.titan = mock_titan
        jarvis.agent_orchestrator = mock_agents
        jarvis.cross_modal = mock_cross_modal
        jarvis.budget = ComputeBudget()
        jarvis.world = None

        jarvis.llm_stream_and_speak = lambda *args, **kwargs: "LLM_REPLY"
        jarvis.deep_research = lambda topic: f"DEEP_RESEARCH({topic})"
        jarvis.get_calendar_events = lambda: "Today: Standup 10:00, Review 15:00"

        jarvis.conversation_history = [{"role": "system", "content": "test"}]

        # 1) Any complex question -> MoA path
        complex_input = "Research quantum networking deeply and thoroughly"
        intent = jarvis.fast_intent(complex_input)
        assert_true(intent.get("intent") == "deep_research", "complex question should map to deep_research")
        r1 = jarvis.process_request(complex_input)
        assert_true(any(c["intent"] == "deep_research" for c in mock_moa.calls), "MoA was not used for complex question")
        print("PASS 1: Complex question uses MoA")

        # 2) Any response -> constitutional filter called
        before_filters = mock_constitution.filter_calls
        _ = jarvis.process_request("moa stats")
        after_filters = mock_constitution.filter_calls
        assert_true(after_filters == before_filters + 1, "Constitutional filter did not run on response")
        print("PASS 2: Constitutional filter runs on responses")

        # 3) titan memory command
        intent = jarvis.fast_intent("titan memory")
        assert_true(intent.get("intent") == "titan_recall", "'titan memory' should map to titan_recall")
        _ = jarvis.process_request("titan memory")
        assert_true("[Titan]:" in jarvis.conversation_history[-2]["content"], "titan recall context not injected")
        print("PASS 3: titan memory command works")

        # 4) moa stats command
        intent = jarvis.fast_intent("moa stats")
        assert_true(intent.get("intent") == "moa_stats", "'moa stats' should map to moa_stats")
        _ = jarvis.process_request("moa stats")
        assert_true("[MoA]:" in jarvis.conversation_history[-2]["content"], "moa stats context not injected")
        print("PASS 4: moa stats command works")

        # 5) agent status command
        intent = jarvis.fast_intent("agent status")
        assert_true(intent.get("intent") == "agent_status", "'agent status' should map to agent_status")
        _ = jarvis.process_request("agent status")
        assert_true("[Agent Graph]:" in jarvis.conversation_history[-2]["content"], "agent status context not injected")
        print("PASS 5: agent status command works")

        # 6) research X using agents -> dispatch to research agent in background
        cmd = "research lithium battery safety using agents"
        intent = jarvis.fast_intent(cmd)
        assert_true(intent.get("intent") == "agent_research", "'research X using agents' should map to agent_research")
        _ = jarvis.process_request(cmd)
        assert_true(len(mock_agents.dispatched) >= 1, "agent dispatch did not happen")
        last_dispatch = mock_agents.dispatched[-1]
        assert_true(last_dispatch["agent_name"] == "research", "dispatch should target research agent")
        assert_true(last_dispatch["wait_secs"] == 0, "dispatch should be background (wait_secs=0)")
        print("PASS 6: research using agents dispatch works")

        # 7) compute budget stats command
        intent = jarvis.fast_intent("compute budget")
        assert_true(intent.get("intent") == "budget_stats", "'compute budget' should map to budget_stats")
        _ = jarvis.process_request("compute budget")
        assert_true("[Compute Budget]:" in jarvis.conversation_history[-2]["content"], "compute budget context not injected")
        print("PASS 7: compute budget command works")

        # 8) constitutional filter stats command
        intent = jarvis.fast_intent("constitutional filter stats")
        assert_true(intent.get("intent") == "filter_stats", "'constitutional filter stats' should map to filter_stats")
        _ = jarvis.process_request("constitutional filter stats")
        assert_true("[Constitutional Filter]:" in jarvis.conversation_history[-2]["content"], "filter stats context not injected")
        print("PASS 8: constitutional filter stats command works")

        # 9) what's my calendar today -> calendar + cross-modal fused
        intent = jarvis.fast_intent("what's my calendar today")
        assert_true(intent.get("intent") == "calendar", "calendar command should map to calendar intent")
        _ = jarvis.process_request("what's my calendar today")
        user_payload = jarvis.conversation_history[-2]["content"]
        assert_true("[Calendar]:" in user_payload, "calendar context not injected")
        assert_true("[CROSS-MODAL]" in user_payload, "cross-modal context not fused into prompt")
        print("PASS 9: calendar + cross-modal fusion command works")

        print("=" * 72)
        print("ALL STRICT PHASE 18 COMMAND TESTS PASSED (9/9)")
        print("=" * 72)
        return 0

    finally:
        # Restore globals.
        jarvis.moa = originals["moa"]
        jarvis.constitution = originals["constitution"]
        jarvis.titan = originals["titan"]
        jarvis.agent_orchestrator = originals["agent_orchestrator"]
        jarvis.cross_modal = originals["cross_modal"]
        jarvis.budget = originals["budget"]
        jarvis.llm_stream_and_speak = originals["llm_stream_and_speak"]
        jarvis.deep_research = originals["deep_research"]
        jarvis.get_calendar_events = originals["get_calendar_events"]
        jarvis.world = originals["world"]
        jarvis.conversation_history = originals["conversation_history"]


if __name__ == "__main__":
    raise SystemExit(run())
