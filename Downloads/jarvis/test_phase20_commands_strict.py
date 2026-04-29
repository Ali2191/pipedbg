#!/usr/bin/env python3
"""
Strict Phase 20 command tests.
Covers command routing + process_request behavior for:
- persistent execution threads
- causal reasoning
- built-in and custom skills
- thread status
- alignment checks
- multimodal screenshot retrieval
"""

import sys

sys.path.insert(0, "/Users/tayyab/Projects/jarvis")

import jarvis


class MockMoA:
    def generate(self, conversation_history, user_input, intent="chat"):
        return f"MOA_REPLY({intent})"


class MockConstitution:
    def filter(self, response, user_input, context=""):
        return response, False


class MockGUI:
    def add_log(self, *args, **kwargs):
        return None

    def set_state(self, *args, **kwargs):
        return None

    def set_intent(self, *args, **kwargs):
        return None


class MockSkill:
    def __init__(self, name):
        self.name = name


class MockSkillLibrary:
    def __init__(self):
        self.skills = []
        self.last_created = None
        self.executed = []

    def match(self, user_input):
        t = user_input.lower()
        if "morning routine" in t:
            return MockSkill("Morning Routine")
        if "commit my code" in t:
            return MockSkill("Code Commit")
        return None

    def execute_skill(self, skill):
        self.executed.append(skill.name)
        return f"Skill '{skill.name}' complete"

    def list_skills(self):
        return "Skill library (3 skills): Morning Routine, Code Commit, Deep Work"

    def create_skill_from_sequence(self, name, description, triggers, steps):
        self.last_created = {
            "name": name,
            "description": description,
            "triggers": triggers,
            "steps": steps,
        }
        self.skills.append(name)
        return MockSkill(name)


class MockExecutor:
    def __init__(self):
        self.created_threads = []

    def parse_and_create(self, user_input):
        if "overnight" in user_input.lower() or "by tomorrow" in user_input.lower():
            thread = type("T", (), {"thread_id": "t_demo_1", "title": "AI papers overnight thread"})
            self.created_threads.append({"input": user_input, "thread": thread})
            return thread
        return None

    def get_active_summary(self):
        return "Active threads:\n  [sleeping] AI papers overnight thread: 40% complete (session #1)"


class MockCausal:
    def analyze_request(self, user_input):
        t = user_input.lower()
        if "why did my code fail" in t:
            return "Likely because a dependency mismatch caused runtime import errors."
        if "what would happen if" in t and "vim" in t:
            return "If you switched to vim: faster keyboard-only workflows after a short adaptation period."
        return None


class MockAlignment:
    def __init__(self):
        self.calls = 0

    def is_due(self):
        return False

    def run_checkpoint(self, memory_stack=None, safety_foundation=None):
        self.calls += 1
        return {
            "timestamp": "2026-04-29T00:00:00",
            "period_days": 90,
            "principles": [],
            "l4_audit": {},
            "correction_count": 0,
            "drift_detected": False,
            "recommendations": [],
        }

    def format_report(self, report):
        return "=== JARVIS ALIGNMENT CHECKPOINT ===\nDrift detected: No"


class MockMM:
    def retrieve_with_screenshots(self, query, n=3):
        return f"[Screenshot from 2026-04-22]: weekly architecture review ({query})"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


class Phase20StrictSuite:
    def __init__(self):
        self.originals = {}
        self.gui = MockGUI()

    def setup(self):
        self.originals = {
            "moa": jarvis.moa,
            "constitution": jarvis.constitution,
            "skill_lib": jarvis.skill_lib,
            "executor": jarvis.executor,
            "causal": jarvis.causal,
            "alignment": jarvis.alignment,
            "mm_memory": jarvis.mm_memory,
            "conversation_history": list(jarvis.conversation_history),
            "budget": jarvis.budget,
            "benchmarks": jarvis.benchmarks,
            "cross_modal": jarvis.cross_modal,
            "permission_policy": jarvis.permission_policy,
            "world": jarvis.world,
        }

        jarvis.moa = MockMoA()
        jarvis.constitution = MockConstitution()
        jarvis.skill_lib = MockSkillLibrary()
        jarvis.executor = MockExecutor()
        jarvis.causal = MockCausal()
        jarvis.alignment = MockAlignment()
        jarvis.mm_memory = MockMM()
        jarvis.budget = None
        jarvis.benchmarks = None
        jarvis.cross_modal = None
        jarvis.permission_policy = None
        jarvis.world = None
        jarvis.conversation_history = [{"role": "system", "content": "seed"}]

    def teardown(self):
        for key, value in self.originals.items():
            setattr(jarvis, key, value)

    def reset_history(self):
        jarvis.conversation_history = [{"role": "system", "content": "seed"}]

    def run(self):
        print("=" * 72)
        print("STRICT PHASE 20 COMMAND TEST")
        print("=" * 72)

        try:
            self.setup()

            cmd = "research AI papers overnight and brief me tomorrow"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "chat", "long-horizon request should remain chat intent")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("Persistent thread" in reply, "persistent thread should be created for overnight task")
            assert_true(jarvis.executor.created_threads, "executor.parse_and_create was not called")
            print("PASS 1: overnight research creates persistent thread")

            self.reset_history()
            cmd = "why did my code fail"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "causal_query", "causal why question should map to causal_query")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("[Causal]:" in reply, "causal explanation should be injected")
            print("PASS 2: why did my code fail routes to causal reasoning")

            self.reset_history()
            cmd = "what would happen if I switched to vim"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "causal_query", "counterfactual should map to causal_query")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("[Causal]:" in reply, "counterfactual prediction should be injected")
            print("PASS 3: vim switch question returns L2 causal prediction")

            self.reset_history()
            cmd = "morning routine"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "skill_run", "morning routine should map to skill_run")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("Morning Routine" in reply, "morning routine built-in skill should execute")
            print("PASS 4: morning routine built-in skill executes")

            self.reset_history()
            cmd = "commit my code"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "skill_run", "commit my code should map to skill_run")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("Code Commit" in reply, "code commit built-in skill should execute")
            print("PASS 5: commit my code built-in skill executes")

            self.reset_history()
            cmd = "create skill called deep work: open vscode, block notifications, set timer"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "skill_create", "create skill command should map to skill_create")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("created" in reply.lower(), "custom skill should be created")
            assert_true(jarvis.skill_lib.last_created is not None, "skill_create handler did not call create_skill_from_sequence")
            assert_true(jarvis.skill_lib.last_created["name"].lower() == "deep work", "skill name parsing failed")
            print("PASS 6: custom skill creation works")

            self.reset_history()
            cmd = "list my skills"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "skill_list", "list my skills should map to skill_list")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("Skill library" in reply, "skills list should be shown")
            print("PASS 7: list my skills works")

            self.reset_history()
            cmd = "execution threads"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "thread_status", "execution threads should map to thread_status")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("Active threads" in reply, "thread status should be shown")
            print("PASS 8: execution threads status works")

            self.reset_history()
            cmd = "run alignment check"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "alignment", "run alignment check should map to alignment")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("ALIGNMENT CHECKPOINT" in reply, "alignment report should run on explicit command")
            assert_true(jarvis.alignment.calls == 1, "explicit run alignment check should force checkpoint run")
            print("PASS 9: run alignment check executes checkpoint")

            self.reset_history()
            cmd = "find that screenshot from last week"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "mm_retrieve", "screenshot retrieval should map to mm_retrieve")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("[Screenshot" in reply, "multimodal screenshot retrieval should return screenshot context")
            print("PASS 10: screenshot retrieval command works")

            print("=" * 72)
            print("ALL STRICT PHASE 20 COMMAND TESTS PASSED (10/10)")
            print("=" * 72)
            return 0
        finally:
            self.teardown()


if __name__ == "__main__":
    raise SystemExit(Phase20StrictSuite().run())
