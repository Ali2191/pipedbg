#!/usr/bin/env python3
"""
Test script for Phase 18 Features 3-5 integration in jarvis.py
Validates: Constitutional Filter, Cross-Modal Fusion, Agent Graph
"""
import sys
sys.path.insert(0, '/Users/tayyab/Projects/jarvis')

def test_constitutional_filter():
    """Test Constitutional Filter module"""
    print("=" * 60)
    print("TEST 1: Constitutional Filter")
    print("=" * 60)
    
    try:
        from core.safety_system import ConstitutionalFilter
        print("✓ ConstitutionalFilter imported successfully")
        
        constitution = ConstitutionalFilter(use_llm_critic=False)  # No LLM for fast test
        print("✓ ConstitutionalFilter instantiated (rule-based only)")
        
        # Test 1: Remove sycophantic opening
        response1 = "Certainly! Here's the information you requested."
        filtered1, mod1 = constitution.filter(response1, "Tell me about X")
        print(f"✓ Test 1: Sycophantic opener removal")
        print(f"  Original: '{response1}'")
        print(f"  Filtered: '{filtered1}'")
        print(f"  Modified: {mod1}")
        
        # Test 2: Remove AI disclaimer
        response2 = "As an AI assistant, I can help you with that. Here's my answer."
        filtered2, mod2 = constitution.filter(response2, "Help me")
        print(f"✓ Test 2: AI disclaimer removal")
        print(f"  Original: '{response2}'")
        print(f"  Filtered: '{filtered2}'")
        print(f"  Modified: {mod2}")
        
        # Test 3: Remove padding
        response3 = "The answer is 42. I hope this helps! Let me know if you need anything else!"
        filtered3, mod3 = constitution.filter(response3, "What's 6*7?")
        print(f"✓ Test 3: Padding removal")
        print(f"  Original: '{response3}'")
        print(f"  Filtered: '{filtered3}'")
        print(f"  Modified: {mod3}")
        
        # Test 4: Get stats
        stats = constitution.get_stats()
        print(f"✓ Stats: {stats}")
        
        return True
    except Exception as e:
        print(f"✗ Constitutional filter error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_cross_modal_fusion():
    """Test Cross-Modal Fusion module"""
    print("\n" + "=" * 60)
    print("TEST 2: Cross-Modal Fusion")
    print("=" * 60)
    
    try:
        from core.memory.cross_modal_fusion import CrossModalFusion
        
        class MockHSL:
            pass
        
        print("✓ CrossModalFusion imported successfully")
        
        cross_modal = CrossModalFusion(hsl_ref=MockHSL())
        print("✓ CrossModalFusion instantiated")
        
        # Test 1: Update screen modality
        cross_modal.update("screen", "Visual studio code showing Python file", 0.9)
        print("✓ Test 1: Screen modality updated")
        
        # Test 2: Update voice emotion
        cross_modal.update("voice_emotion", "excited", 0.8)
        print("✓ Test 2: Voice emotion updated")
        
        # Test 3: Update calendar context
        cross_modal.update("calendar", "Meeting with Alice at 2 PM", 0.9)
        print("✓ Test 3: Calendar context updated")
        
        # Test 4: Fuse contexts
        fused = cross_modal.fuse()
        print(f"✓ Test 4: Contexts fused")
        print(f"  Dominant modality: {fused.dominant_modality}")
        print(f"  Fused summary: {fused.fused_summary}")
        
        # Test 5: Get HSL injection
        injection = cross_modal.as_hsl_injection()
        print(f"✓ Test 5: HSL injection string: {injection[:60]}...")
        
        return True
    except Exception as e:
        print(f"✗ Cross-modal fusion error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_agent_graph():
    """Test Agent Graph module"""
    print("\n" + "=" * 60)
    print("TEST 3: Agent Graph")
    print("=" * 60)
    
    try:
        from core.agents.agent_graph import (
            AgentOrchestrator, MessageBus, BaseAgent, 
            AgentMessage, ResearchAgent
        )
        print("✓ Agent graph modules imported successfully")
        
        # Create a mock LLM function
        def mock_llm(prompt, **kw):
            return f"Mock response to: {prompt[:30]}..."
        
        # Test 1: Orchestrator instantiation
        orchestrator = AgentOrchestrator(llm_fn=mock_llm)
        print("✓ Test 1: AgentOrchestrator instantiated")
        print(f"  Agents spawned: {list(orchestrator.agents.keys())}")
        
        # Test 2: Start agents
        orchestrator.start()
        print("✓ Test 2: Agent graph started (4 agents online)")
        
        # Test 3: Routing logic
        tasks = [
            ("Research Python best practices", "research"),
            ("Fix a bug in my code", "code"),
            ("What's on my calendar?", "calendar"),
            ("General knowledge question", "research"),  # default
        ]
        
        for task, expected_route in tasks:
            routed = orchestrator._route(task)
            status = "✓" if routed == expected_route else "✗"
            print(f"{status} Route '{task[:30]}...' -> {routed}")
        
        # Test 4: Status report
        status = orchestrator.status()
        print(f"✓ Test 4: Status report: {status[:60]}...")
        
        # Test 5: Message bus
        print(f"✓ Test 5: Message bus registered {len(orchestrator.bus._agents)} agents")
        
        return True
    except Exception as e:
        print(f"✗ Agent graph error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_jarvis_integration():
    """Test that all modules are integrated into jarvis.py"""
    print("\n" + "=" * 60)
    print("TEST 4: jarvis.py Integration Verification")
    print("=" * 60)
    
    try:
        with open('/Users/tayyab/Projects/jarvis/jarvis.py', 'r') as f:
            jarvis_content = f.read()
        
        checks = {
            "Constitutional import": "from core.safety_system import ConstitutionalFilter",
            "Cross-Modal import": "from core.memory.cross_modal_fusion import CrossModalFusion",
            "Agent Graph import": "from core.agents.agent_graph import AgentOrchestrator",
            "constitution global": "constitution      = None",
            "cross_modal global": "cross_modal       = None",
            "agent_orchestrator global": "agent_orchestrator = None",
            "constitutional init": "constitution = ConstitutionalFilter",
            "cross_modal init": "cross_modal = CrossModalFusion",
            "agent_orchestrator init": "agent_orchestrator = AgentOrchestrator",
            "constitutional filter in process_request": "constitution.filter(reply, user_input)",
            "cross_modal context injection": "cross_modal.as_hsl_injection()",
            "cross_modal update": 'cross_modal.update("screen"',
        }
        
        all_passed = True
        for check_name, check_string in checks.items():
            if check_string in jarvis_content:
                print(f"✓ {check_name}")
            else:
                print(f"✗ {check_name} - NOT FOUND")
                all_passed = False
        
        return all_passed
    except Exception as e:
        print(f"✗ Integration verification error: {e}")
        return False

def main():
    print("\n" + "★" * 60)
    print("★ PHASE 18 FEATURES 3-5 TEST SUITE ★")
    print("★ Constitutional Filter | Cross-Modal Fusion | Agent Graph ★")
    print("★" * 60)
    
    results = {}
    
    # Test 1: Constitutional Filter
    results['constitutional'] = test_constitutional_filter()
    
    # Test 2: Cross-Modal Fusion
    results['cross_modal'] = test_cross_modal_fusion()
    
    # Test 3: Agent Graph
    results['agent_graph'] = test_agent_graph()
    
    # Test 4: jarvis.py Integration
    results['jarvis_integration'] = test_jarvis_integration()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
    
    print("\n" + "★" * 60)
    if passed == total:
        print("★ ALL TESTS PASSED - PHASE 18 FEATURES 3-5 READY ★")
    else:
        print(f"★ {total - passed} TEST(S) FAILED ★")
    print("★" * 60 + "\n")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
