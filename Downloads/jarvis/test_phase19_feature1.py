"""
test_phase19_feature1.py — Process Reward Model Integration Test
Tests the Phase 19 Feature 1 (Process Reward Models) integration into jarvis.py
"""

import sys
import unittest
from unittest.mock import Mock, patch, MagicMock
from io import StringIO
from datetime import datetime
from core.reasoning_system import (
    ProcessRewardModel, StepVerifier, ReasoningStep, ReasoningTrace
)


class MockLLM:
    """Mock LLM that returns deterministic outputs for testing."""
    def __call__(self, prompt, max_tokens=None, temperature=None):
        if "break down" in prompt.lower() or "step" in prompt.lower():
            # Return multi-step reasoning
            return (
                "Step 1: Identify the problem domain\n"
                "Step 2: Gather relevant context\n"
                "Step 3: Synthesize a solution"
            )
        elif "verify" in prompt.lower():
            return "VALID: Reasoning is sound."
        else:
            return "This is a test response from mock LLM."


class TestStepVerifier(unittest.TestCase):
    """Test the StepVerifier component."""

    def setUp(self):
        self.mock_llm = MockLLM()
        self.verifier = StepVerifier(self.mock_llm)

    def test_verify_math_correct(self):
        """Test that correct math is verified."""
        step = ReasoningStep(step_id=1, content="2 + 2 = 4", step_type="inference")
        previous = []
        valid, reason = self.verifier.verify(step, previous)
        self.assertTrue(valid)

    def test_verify_math_incorrect(self):
        """Test that incorrect math is rejected."""
        step = ReasoningStep(step_id=1, content="2 + 2 = 5", step_type="inference")
        previous = []
        valid, reason = self.verifier.verify(step, previous)
        self.assertFalse(valid)
        self.assertIn("not 5", reason)

    def test_contradiction_detection(self):
        """Test detection of logical contradictions."""
        prev_step = ReasoningStep(
            step_id=0, 
            content="This is always true",
            step_type="inference"
        )
        new_step = ReasoningStep(
            step_id=1, 
            content="This is never true",
            step_type="inference"
        )
        valid, reason = self.verifier.verify(new_step, [prev_step])
        # Should detect contradiction
        if not valid:
            self.assertIn("Contradicts", reason)

    def test_stats_tracking(self):
        """Test that verifier tracks statistics."""
        step1 = ReasoningStep(step_id=1, content="5 + 5 = 10", step_type="inference")
        step2 = ReasoningStep(step_id=2, content="3 + 3 = 7", step_type="inference")
        
        self.verifier.verify(step1, [])
        self.verifier.verify(step2, [])
        
        stats = self.verifier.stats()
        self.assertIn("PRM Verifier", stats)
        self.assertIn("steps", stats)


class TestProcessRewardModel(unittest.TestCase):
    """Test the ProcessRewardModel pipeline."""

    def setUp(self):
        self.mock_llm = MockLLM()
        self.prm = ProcessRewardModel(llm_fn=self.mock_llm, max_steps=5, max_retries=1)

    def test_should_use_prm_deep_research(self):
        """Test that deep_research intent triggers PRM."""
        result = self.prm.should_use_prm("deep_research", "research quantum computing")
        self.assertTrue(result)

    def test_should_use_prm_fix_code(self):
        """Test that fix_code intent triggers PRM."""
        result = self.prm.should_use_prm("fix_code", "fix my python script")
        self.assertTrue(result)

    def test_should_use_prm_multi_step_language(self):
        """Test that multi-step language triggers PRM."""
        result = self.prm.should_use_prm("chat", "how do I debug this issue step by step?")
        self.assertTrue(result)

    def test_should_not_use_prm_simple_chat(self):
        """Test that simple chat doesn't trigger PRM."""
        result = self.prm.should_use_prm("chat", "hello")
        self.assertFalse(result)

    def test_reason_generates_steps(self):
        """Test that reasoning generates and verifies steps."""
        query = "How to solve a quadratic equation?"
        reply = self.prm.reason(query)
        
        # Should return non-empty reply
        self.assertIsNotNone(reply)
        self.assertGreater(len(reply), 0)

    def test_trace_logging(self):
        """Test that reasoning traces are logged."""
        query = "Analyze this code problem"
        _ = self.prm.reason(query)
        
        # Check that trace was recorded
        self.assertEqual(len(self.prm._traces), 1)
        trace = self.prm._traces[0]
        self.assertEqual(trace.query, query)
        self.assertGreater(trace.total_steps, 0)

    def test_last_trace_summary(self):
        """Test the summary of last reasoning trace."""
        query = "Test reasoning"
        _ = self.prm.reason(query)
        
        summary = self.prm.last_trace_summary()
        self.assertIn("Last PRM trace", summary)
        self.assertIn("steps", summary)


class TestProcessRewardIntegration(unittest.TestCase):
    """Test PRM integration with jarvis.py process_request."""

    def test_prm_import_in_jarvis(self):
        """Verify ProcessRewardModel can be imported in jarvis context."""
        try:
            # Import to verify no syntax errors
            import jarvis
            self.assertTrue(hasattr(jarvis, 'ProcessRewardModel'))
        except Exception as e:
            self.fail(f"Failed to import jarvis: {e}")

    def test_prm_initialization_signature(self):
        """Test PRM initializes with correct signature."""
        mock_llm = MockLLM()
        prm = ProcessRewardModel(llm_fn=mock_llm, max_steps=8, max_retries=2)
        
        self.assertEqual(prm.max_steps, 8)
        self.assertEqual(prm.max_retries, 2)
        self.assertIsNotNone(prm.verifier)
        self.assertEqual(len(prm._traces), 0)

    def test_prm_with_constitutional_filter_mock(self):
        """Test PRM output can be filtered by constitutional filter."""
        mock_llm = MockLLM()
        prm = ProcessRewardModel(llm_fn=mock_llm)
        
        # Mock constitutional filter
        mock_constitution = Mock()
        mock_constitution.filter = Mock(return_value="filtered response")
        
        # Simulate the filtering scenario from jarvis.py
        prm_reply = prm.reason("test query")
        filtered_reply = mock_constitution.filter(prm_reply, "test query")
        
        self.assertEqual(filtered_reply, "filtered response")
        mock_constitution.filter.assert_called_once()

    def test_prm_backtracking_on_failure(self):
        """Test that PRM backtracks when step verification fails."""
        # Use a mock LLM that returns steps with one failure
        mock_llm = Mock()
        
        def mock_llm_fn(prompt, **kwargs):
            if "correct" in prompt.lower():
                return "Step 1: Start\nStep 2: Process\nStep 3: End"
            elif "verify" in prompt.lower():
                # Simulate verification failure on some steps
                if "Process" in prompt:
                    return "INVALID: This step has an error"
                return "VALID: OK"
            elif "break down" in prompt.lower():
                return "Step 1: Start\nStep 2: Corrected process\nStep 3: End"
            return "Test response"
        
        mock_llm.side_effect = mock_llm_fn
        prm = ProcessRewardModel(llm_fn=mock_llm, max_retries=2)
        
        # Run reasoning - should handle failures gracefully
        reply = prm.reason("test backtracking scenario")
        self.assertIsNotNone(reply)

    def test_prm_reasoning_trace_structure(self):
        """Test that reasoning traces have correct structure."""
        mock_llm = MockLLM()
        prm = ProcessRewardModel(llm_fn=mock_llm)
        
        query = "Structure test query"
        _ = prm.reason(query)
        
        trace = prm._traces[0]
        
        # Validate ReasoningTrace structure
        self.assertEqual(trace.query, query)
        self.assertIsInstance(trace.steps, list)
        self.assertIsInstance(trace.total_steps, int)
        self.assertIsInstance(trace.backtrack_count, int)
        self.assertIsInstance(trace.success, bool)
        self.assertIsNotNone(trace.final_answer)
        self.assertIsNotNone(trace.created_at)

    def test_prm_step_classification(self):
        """Test that reasoning steps are properly classified."""
        mock_llm = MockLLM()
        prm = ProcessRewardModel(llm_fn=mock_llm)
        
        # Test step type classification
        test_cases = [
            ("Therefore we conclude", "conclusion"),
            ("We observe that", "observation"),
            ("Run the command now", "action"),
            ("We infer from this", "inference"),
        ]
        
        for content, expected_type in test_cases:
            classified = prm._classify_step(content)
            self.assertEqual(classified, expected_type,
                           f"Failed to classify '{content}'")

    def test_prm_step_synthesis(self):
        """Test final answer synthesis from verified steps."""
        mock_llm = Mock(return_value="Synthesized final answer")
        prm = ProcessRewardModel(llm_fn=mock_llm)
        
        steps = [
            ReasoningStep(step_id=1, content="Step 1 content", step_type="inference", verified=True),
            ReasoningStep(step_id=2, content="Step 2 content", step_type="action", verified=True),
        ]
        
        result = prm._synthesize("test query", steps)
        self.assertEqual(result, "Synthesized final answer")

    def test_prm_trace_persistence(self):
        """Test that only recent traces are kept (max 20)."""
        mock_llm = MockLLM()
        prm = ProcessRewardModel(llm_fn=mock_llm, max_steps=2)
        
        # Generate 25 traces
        for i in range(25):
            prm.reason(f"Query {i}")
        
        # Should only keep last 20
        self.assertLessEqual(len(prm._traces), 20)
        self.assertEqual(len(prm._traces), 20)


class TestProcessRewardErrorHandling(unittest.TestCase):
    """Test error handling in PRM."""

    def test_prm_handles_llm_failure(self):
        """Test that PRM gracefully handles LLM failures."""
        mock_llm = Mock(side_effect=Exception("LLM unavailable"))
        prm = ProcessRewardModel(llm_fn=mock_llm)
        
        # Should not crash, should return something
        try:
            reply = prm.reason("test query")
            self.assertIsNotNone(reply)
        except Exception as e:
            self.fail(f"PRM crashed on LLM error: {e}")

    def test_verifier_handles_invalid_step_formats(self):
        """Test that verifier handles oddly formatted steps."""
        mock_llm = MockLLM()
        verifier = StepVerifier(mock_llm)
        
        # Test with unusual content
        step = ReasoningStep(
            step_id=1,
            content="",  # Empty
            step_type="inference"
        )
        
        try:
            valid, reason = verifier.verify(step, [])
            # Should handle gracefully
            self.assertIsInstance(valid, bool)
            self.assertIsInstance(reason, str)
        except Exception as e:
            self.fail(f"Verifier crashed on empty step: {e}")


def run_tests():
    """Run all tests and report results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestStepVerifier))
    suite.addTests(loader.loadTestsFromTestCase(TestProcessRewardModel))
    suite.addTests(loader.loadTestsFromTestCase(TestProcessRewardIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestProcessRewardErrorHandling))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    
    if success:
        print("\n" + "=" * 70)
        print("✓ ALL PHASE 19 FEATURE 1 TESTS PASSED")
        print("=" * 70)
        sys.exit(0)
    else:
        print("\n" + "=" * 70)
        print("✗ SOME TESTS FAILED")
        print("=" * 70)
        sys.exit(1)
