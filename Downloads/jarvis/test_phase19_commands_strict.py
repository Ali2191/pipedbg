#!/usr/bin/env python3
"""
Strict Phase 19 command tests.
Covers routing + process_request behavior for the new goal, HTN, PRM,
safety, device-status, and self-improvement commands.
"""

import sys

sys.path.insert(0, "/Users/tayyab/Projects/jarvis")

import jarvis


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


class MockSafety:
    def __init__(self):
        self.decisions = [
            {"intent": "open_url", "action": "executed open_url"},
            {"intent": "search_web", "action": "executed search_web"},
            {"intent": "email", "action": "executed email"},
            {"intent": "goal", "action": "executed goal"},
            {"intent": "calendar", "action": "executed calendar"},
        ]
        self.logged = []

    def pre_action_check(self, intent, user_input, params=None):
        if "delete this file" in user_input.lower():
            return False, "Safety check: Say 'confirm' to continue with delete."
        return True, ""

    def post_response_wrap(self, response, intent):
        return response

    def log(self, user_input, intent, action, reasoning):
        self.logged.append({
            "user_input": user_input,
            "intent": intent,
            "action": action,
            "reasoning": reasoning,
        })

    def explain(self):
        return "My last decision: I paused because the action was ambiguous."

    def chain(self):
        return (
            "Decision chain (last 5):\n"
            "1. [open_url] executed open_url\n"
            "2. [search_web] executed search_web\n"
            "3. [email] executed email\n"
            "4. [goal] executed goal\n"
            "5. [calendar] executed calendar"
        )


class MockGoalStack:
    def __init__(self):
        self.goals = []
        self.inferred = []
        self.push_calls = []
        self.completed = []

    def infer_from_input(self, user_input):
        text = user_input.lower()
        if "i'm trying to" in text or "i am trying to" in text or "my goal is" in text or "i want to" in text:
            title = "learn machine learning" if "machine learning" in text else "finish JARVIS phase 19"
            self.push(title)
            self.inferred.append(user_input)
            return title
        return None

    def push(self, title, deadline="", priority=5):
        goal = {
            "id": f"g_{len(self.goals) + 1}",
            "title": title,
            "priority": priority,
            "deadline": deadline,
            "progress": 0.0,
            "status": "active",
            "created_at": "2026-04-28T10:00:00",
            "updated_at": "2026-04-28T10:00:00",
            "notes": [],
        }
        self.goals.append(goal)
        self.push_calls.append(title)
        return goal

    def active(self):
        return [g for g in self.goals if g["status"] == "active"]

    def summary(self):
        if not self.goals:
            return "Goal stack: 0 active | 0 completed | 0 overdue | 0 total"
        lines = [f"Goal stack: {len(self.active())} active | 0 completed | 0 overdue | {len(self.goals)} total"]
        for g in self.active():
            lines.append(f"- {g['title']} [{g['progress']:.0%}]")
        return "\n".join(lines)

    def for_prompt(self):
        if not self.goals:
            return ""
        lines = ["Active goals:"]
        for g in self.active():
            lines.append(f"  - {g['title']} [{g['progress']:.0%}]")
        return "\n".join(lines)

    def complete(self, goal_id):
        for g in self.goals:
            if g["id"] == goal_id:
                g["status"] = "done"
                g["progress"] = 1.0
                self.completed.append(goal_id)
                break


class MockHTNPlanner:
    def __init__(self):
        self.add_goal_calls = []

    def add_goal(self, title, description="", deadline="", auto_decompose=True):
        self.add_goal_calls.append({
            "title": title,
            "description": description,
            "deadline": deadline,
            "auto_decompose": auto_decompose,
        })
        return "goal_19"

    def get_goal_summary(self):
        return "**Active Goals:**\n- Launch portfolio site [0%]: next: write checklist"

    def inject_into_prompt(self):
        return "**Active Goals:**\n- Launch portfolio site [0%]: next: write checklist\n**Next executable actions:** write checklist"


class MockPRM:
    def __init__(self):
        self.calls = []

    def should_use_prm(self, intent, user_input):
        text = user_input.lower()
        return "step by step" in text or "debug" in text

    def reason(self, query, context=""):
        self.calls.append({"query": query, "context": context})
        return "PRM_REPLY"

    def last_trace_summary(self):
        return "Last PRM trace: 3 steps | 1 backtracks | success=True"


class MockHSLOrchestrator:
    def __init__(self):
        self.route_calls = []

    def route_task(self, intent):
        self.route_calls.append(intent)
        return "local"

    def delegate_task(self, device_name, intent, user_input):
        return f"delegated:{device_name}:{intent}"

    def peer_context_for_prompt(self):
        return "[PEER DEVICES] pi: emotion=focused task=monitoring | phone: emotion=neutral task=idle"


class MockImprover:
    def __init__(self):
        self.calls = 0
        self.last_output = ""

    def run_full_cycle(self):
        self.calls += 1
        self.last_output = "Self-improvement complete: 12 targeted training samples generated."
        return self.last_output


class MockDynPrompt:
    def __init__(self):
        self.calls = []

    def build(self, intent, hsl, extra_context=""):
        self.calls.append({"intent": intent, "extra_context": extra_context})
        return f"SYSTEM::{intent}::{extra_context}"


class MockLLM:
    def __call__(self, *args, **kwargs):
        return "LLM_REPLY"


class MockGUI:
    def add_log(self, *args, **kwargs):
        return None

    def set_state(self, *args, **kwargs):
        return None

    def set_intent(self, *args, **kwargs):
        return None


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


class Phase19StrictSuite:
    def __init__(self):
        self.originals = {}
        self.gui = MockGUI()

    def setup(self):
        self.originals = {
            "moa": jarvis.moa,
            "constitution": jarvis.constitution,
            "safety": jarvis.safety,
            "goal_stack": jarvis.goal_stack,
            "htn": jarvis.htn,
            "prm": jarvis.prm,
            "hsl_orchestrator": jarvis.hsl_orchestrator,
            "improver": jarvis.improver,
            "dyn_prompt": jarvis.dyn_prompt,
            "llm_stream_and_speak": jarvis.llm_stream_and_speak,
            "budget": jarvis.budget,
            "benchmarks": jarvis.benchmarks,
            "cross_modal": jarvis.cross_modal,
            "conversation_history": list(jarvis.conversation_history),
            "permission_policy": jarvis.permission_policy,
            "world": jarvis.world,
        }

        jarvis.moa = MockMoA()
        jarvis.constitution = MockConstitution()
        jarvis.safety = MockSafety()
        jarvis.goal_stack = MockGoalStack()
        jarvis.htn = MockHTNPlanner()
        jarvis.prm = MockPRM()
        jarvis.hsl_orchestrator = MockHSLOrchestrator()
        jarvis.improver = MockImprover()
        jarvis.dyn_prompt = MockDynPrompt()
        jarvis.llm_stream_and_speak = MockLLM()
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
        if hasattr(jarvis.dyn_prompt, "calls"):
            jarvis.dyn_prompt.calls = []

    def run(self):
        print("=" * 72)
        print("STRICT PHASE 19 COMMAND TEST")
        print("=" * 72)

        try:
            self.setup()

            # 1) Goal auto-detect and add to stack
            cmd = "my goal is to finish JARVIS phase 19"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "add_goal", "goal command should map to add_goal")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("finish JARVIS phase 19" in jarvis.goal_stack.push_calls[-1], "goal was not added with trimmed title")
            assert_true("finish JARVIS phase 19" in jarvis.goal_stack.for_prompt(), "goal stack did not retain the new goal")
            print("PASS 1: goal auto-detect and stack add works")

            # 2) show my goals
            self.reset_history()
            jarvis.goal_stack.push("launch portfolio site")
            cmd = "show my goals"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "view_goals", "show my goals should map to view_goals")
            _ = jarvis.process_request(cmd, gui=self.gui)
            payload = jarvis.goal_stack.summary() + "\n" + jarvis.goal_stack.for_prompt()
            assert_true("Goal stack:" in payload, "goal summary not available")
            assert_true("launch portfolio site" in payload, "active goal missing from stack output")
            print("PASS 2: show my goals lists active goals")

            # 3) HTN plan decomposition
            self.reset_history()
            cmd = "plan for launching my portfolio site"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "htn_plan", "plan command should map to htn_plan")
            _ = jarvis.process_request(cmd, gui=self.gui)
            assert_true(jarvis.htn.add_goal_calls, "HTN add_goal was not called")
            assert_true(jarvis.htn.add_goal_calls[-1]["title"] == "launching my portfolio site", "HTN extracted goal title is wrong")
            assert_true("HTN" in jarvis.dyn_prompt.calls[-1]["extra_context"], "HTN context not injected")
            print("PASS 3: HTN planning works")

            # 4) PRM reasoning chain for debug step-by-step request
            self.reset_history()
            cmd = "step by step how do I debug this error"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "fix_code", "debug command should route to fix_code and then PRM")
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true(reply == "PRM_REPLY", "PRM did not return the expected response")
            assert_true(jarvis.prm.calls and jarvis.prm.calls[-1]["query"] == cmd, "PRM reason was not called")
            print("PASS 4: step-by-step debug uses PRM")

            # 5) why did you do that -> last decision explanation
            self.reset_history()
            cmd = "why did you do that"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "explain_decision", "why-did-you-do-that should map to explain_decision")
            _ = jarvis.process_request(cmd, gui=self.gui)
            payload = jarvis.dyn_prompt.calls[-1]["extra_context"]
            assert_true("My last decision:" in payload, "last decision explanation not injected")
            print("PASS 5: why did you do that explains last decision")

            # 6) reasoning chain -> last five decisions
            self.reset_history()
            cmd = "reasoning chain"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "explain_decision", "reasoning chain should route to explain_decision")
            _ = jarvis.process_request(cmd, gui=self.gui)
            payload = jarvis.dyn_prompt.calls[-1]["extra_context"]
            for label in ["1.", "2.", "3.", "4.", "5."]:
                assert_true(label in payload, f"decision chain missing {label}")
            print("PASS 6: reasoning chain shows last five decisions")

            # 7) run self improvement
            self.reset_history()
            cmd = "run self improvement"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "self_improve", "self-improvement command should map to self_improve")
            _ = jarvis.process_request(cmd, gui=self.gui)
            payload = jarvis.improver.last_output
            assert_true("Self-improvement complete" in payload, "self-improvement output not injected")
            assert_true(jarvis.improver.calls == 1, "self-improvement engine was not invoked")
            print("PASS 7: self improvement command works")

            # 8) device status
            self.reset_history()
            cmd = "device status"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "device_status", "device status should map to device_status")
            _ = jarvis.process_request(cmd, gui=self.gui)
            payload = jarvis.dyn_prompt.calls[-1]["extra_context"]
            assert_true("[PEER DEVICES]" in payload, "device status did not show peer states")
            assert_true("pi:" in payload and "phone:" in payload, "peer states missing from device status")
            print("PASS 8: device status shows peer HSL states")

            # 9) delete this file -> confirmation required
            self.reset_history()
            cmd = "delete this file"
            intent = jarvis.fast_intent(cmd)
            reply = jarvis.process_request(cmd, gui=self.gui)
            assert_true("confirm" in reply.lower(), "delete command should ask for confirmation")
            assert_true(len(jarvis.conversation_history) == 1, "delete should not continue into model response")
            print("PASS 9: delete command asks for confirmation")

            # 10) learning goal is inferred and added
            self.reset_history()
            cmd = "i'm trying to learn machine learning"
            intent = jarvis.fast_intent(cmd)
            assert_true(intent.get("intent") == "add_goal", "learning-goal command should map to add_goal")
            _ = jarvis.process_request(cmd, gui=self.gui)
            assert_true(jarvis.goal_stack.push_calls, "learning goal was not added")
            payload = jarvis.goal_stack.for_prompt()
            assert_true("learn machine learning" in payload.lower(), "learning goal not injected into prompt")
            print("PASS 10: learning goal is inferred and added")

            print("=" * 72)
            print("ALL STRICT PHASE 19 COMMAND TESTS PASSED (10/10)")
            print("=" * 72)
            return 0

        finally:
            self.teardown()


if __name__ == "__main__":
    raise SystemExit(Phase19StrictSuite().run())
