#!/usr/bin/env python3
"""
Test script for Phase 18 Features 6-7: Dynamic Prompts & Compute Budgeting
"""
import sys
sys.path.insert(0, '/Users/tayyab/Projects/jarvis')

def test_dynamic_prompt_builder():
    """Test Dynamic Prompt Builder module"""
    print("=" * 60)
    print("TEST 1: Dynamic Prompt Builder")
    print("=" * 60)
    
    try:
        from core.reasoning_system import DynamicPromptBuilder
        print("✓ DynamicPromptBuilder imported successfully")
        
        builder = DynamicPromptBuilder(cms_ref=None)
        print("✓ DynamicPromptBuilder instantiated")
        
        # Test 1: Basic prompt build
        prompt1 = builder.build(intent="chat", hsl=None)
        print(f"✓ Test 1: Basic chat prompt built ({len(prompt1)} chars)")
        assert "JARVIS" in prompt1, "Frozen base not in prompt"
        assert "Tony Stark" in prompt1, "JARVIS reference missing"
        
        # Test 2: Deep research intent adds research module
        prompt2 = builder.build(intent="deep_research", hsl=None)
        print(f"✓ Test 2: Deep research prompt built ({len(prompt2)} chars)")
        assert "Research synthesis" in prompt2 or "comprehensive" in prompt2.lower(), "Research module missing"
        
        # Test 3: Code intent adds code module
        prompt3 = builder.build(intent="fix_code", hsl=None)
        print(f"✓ Test 3: Code assistant prompt built ({len(prompt3)} chars)")
        assert "working, complete code" in prompt3.lower() or "code" in prompt3.lower(), "Code module missing"
        
        # Test 4: With extra context
        prompt4 = builder.build(intent="chat", hsl=None, extra_context="User is frustrated")
        print(f"✓ Test 4: Prompt with extra context ({len(prompt4)} chars)")
        assert "User is frustrated" in prompt4, "Extra context not injected"
        
        # Test 5: With proactive flag
        prompt5 = builder.build(intent="chat", hsl=None, is_proactive=True)
        print(f"✓ Test 5: Proactive mode prompt built ({len(prompt5)} chars)")
        assert "proactive" in prompt5.lower() or "interrupting" in prompt5.lower(), "Proactive module missing"
        
        return True
    except Exception as e:
        print(f"✗ Dynamic prompt builder error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_compute_budget():
    """Test Compute Budget module"""
    print("\n" + "=" * 60)
    print("TEST 2: Compute Budget")
    print("=" * 60)
    
    try:
        from core.orchestration.compute_budget import ComputeBudget, ComputeTier
        print("✓ ComputeBudget imported successfully")
        
        budget = ComputeBudget()
        print("✓ ComputeBudget instantiated")
        
        # Test 1: Trivial tier
        tier_time, cfg = budget.get_config("time_query", "What time is it?")
        print(f"✓ Test 1: time_query -> {tier_time.name} (timeout={cfg['timeout']}s)")
        assert tier_time == ComputeTier.TRIVIAL, "time_query should be TRIVIAL"
        assert cfg["use_moa"] == False, "TRIVIAL should not use MoA"
        
        # Test 2: Simple tier
        tier_open, cfg = budget.get_config("open_app", "Open Slack")
        print(f"✓ Test 2: open_app -> {tier_open.name} (timeout={cfg['timeout']}s)")
        assert tier_open == ComputeTier.SIMPLE, "open_app should be SIMPLE"
        assert "1.5b" in cfg["model"], "SIMPLE should use 1.5b model"
        
        # Test 3: Standard tier
        tier_chat, cfg = budget.get_config("chat", "Tell me about Python")
        print(f"✓ Test 3: chat -> {tier_chat.name} (timeout={cfg['timeout']}s)")
        assert tier_chat == ComputeTier.STANDARD, "chat should be STANDARD"
        assert cfg["max_tokens"] == 300, "STANDARD max_tokens should be 300"
        
        # Test 4: Complex tier
        tier_code, cfg = budget.get_config("fix_code", "Fix this Python bug")
        print(f"✓ Test 4: fix_code -> {tier_code.name} (timeout={cfg['timeout']}s)")
        assert tier_code == ComputeTier.COMPLEX, "fix_code should be COMPLEX"
        assert cfg["use_moa"] == True, "COMPLEX should use MoA"
        
        # Test 5: Upgrade for complex user input
        tier_upgrade, cfg = budget.get_config("chat", "Thoroughly explain the implications of quantum computing on cryptography including real-world applications and current challenges")
        print(f"✓ Test 5: Long query upgrade -> {tier_upgrade.name}")
        assert tier_upgrade == ComputeTier.COMPLEX, "Long queries should upgrade tier"
        
        # Test 6: Complex keywords
        tier_keyword, cfg = budget.get_config("search_web", "Give me a comprehensive deeply detailed analysis")
        print(f"✓ Test 6: Keyword upgrade -> {tier_keyword.name}")
        assert tier_keyword == ComputeTier.COMPLEX, "Comprehensive queries should upgrade tier"
        
        # Test 7: Record latencies
        budget.record(ComputeTier.SIMPLE, 150.5)
        budget.record(ComputeTier.SIMPLE, 175.2)
        budget.record(ComputeTier.COMPLEX, 3500.0)
        print("✓ Test 7: Latencies recorded")
        
        # Test 8: Stats
        stats = budget.stats()
        print(f"✓ Test 8: Stats: {stats}")
        assert "SIMPLE" in stats, "Stats should include tracked tiers"
        
        return True
    except Exception as e:
        print(f"✗ Compute budget error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_integration():
    """Test integration into jarvis.py"""
    print("\n" + "=" * 60)
    print("TEST 3: jarvis.py Integration")
    print("=" * 60)
    
    try:
        with open('/Users/tayyab/Projects/jarvis/jarvis.py', 'r') as f:
            jarvis_content = f.read()
        
        checks = {
            "Dynamic prompt import": "from core.reasoning_system import DynamicPromptBuilder",
            "Compute budget import": "from core.orchestration.compute_budget import ComputeBudget, ComputeTier",
            "dyn_prompt global": "dyn_prompt        = None",
            "budget global": "budget            = None",
            "dyn_prompt init": "dyn_prompt = DynamicPromptBuilder",
            "budget init": "budget = ComputeBudget()",
            "budget.get_config": "budget.get_config(action, user_input)",
            "dyn_prompt.build": "dyn_prompt.build(",
            "budget.record": "budget.record(compute_tier",
            "ComputeTier in signature": "compute_tier, tier_config = budget.get_config",
            "MoA condition": "tier_config.get(\"use_moa\") and moa",
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
    print("★ PHASE 18 FEATURES 6-7 TEST SUITE ★")
    print("★ Dynamic Prompts | Compute Budgeting ★")
    print("★" * 60)
    
    results = {}
    
    # Test 1: Dynamic Prompt Builder
    results['dynamic_prompt'] = test_dynamic_prompt_builder()
    
    # Test 2: Compute Budget
    results['compute_budget'] = test_compute_budget()
    
    # Test 3: jarvis.py Integration
    results['integration'] = test_integration()
    
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
        print("★ ALL TESTS PASSED - PHASE 18 FEATURES 6-7 READY ★")
    else:
        print(f"★ {total - passed} TEST(S) FAILED ★")
    print("★" * 60 + "\n")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
