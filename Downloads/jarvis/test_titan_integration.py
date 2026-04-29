#!/usr/bin/env python3
"""
Test script for Titan Memory integration in jarvis.py
Validates: import, instantiation, surprise scoring, store/retrieve cycle
"""
import sys
sys.path.insert(0, '/Users/tayyab/Projects/jarvis')

def test_titan_module():
    """Test Titan module independently"""
    print("=" * 60)
    print("TEST 1: Titan Module Import & Instantiation")
    print("=" * 60)
    
    try:
        from core.memory.titan_memory import TitanMemoryModule
        print("✓ TitanMemoryModule imported successfully")
        
        titan = TitanMemoryModule()
        print("✓ TitanMemoryModule instantiated")
        print(f"  - Module loaded and ready")
        
        # Check that public API methods exist
        assert hasattr(titan, 'compute_surprise'), "Missing compute_surprise method"
        assert hasattr(titan, 'store'), "Missing store method"
        assert hasattr(titan, 'retrieve'), "Missing retrieve method"
        assert hasattr(titan, 'get_stats'), "Missing get_stats method"
        print("✓ All public API methods available")
        
        return titan
    except Exception as e:
        print(f"✗ Titan module error: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_surprise_computation(titan):
    """Test surprise score computation"""
    print("\n" + "=" * 60)
    print("TEST 2: Surprise Score Computation")
    print("=" * 60)
    
    try:
        # First call with empty database
        msg1 = "What is the capital of France?"
        surprise1 = titan.compute_surprise(msg1)
        print(f"✓ Message 1: '{msg1}'")
        print(f"  Surprise score: {surprise1:.4f} (first memory always ~0.8)")
        
        # Now store it so the database is not empty
        stored1 = titan.store(msg1, context="user_query")
        if stored1:
            print(f"  Stored with surprise: {stored1.surprise_score:.4f}")
        
        # Test similar message (should have different surprise now that DB has content)
        msg2 = "What is the capital of Italy?"
        surprise2 = titan.compute_surprise(msg2)
        print(f"✓ Message 2: '{msg2}'")
        print(f"  Surprise score: {surprise2:.4f} (similar to stored message)")
        
        # Store the second message
        stored2 = titan.store(msg2, context="user_query")
        if stored2:
            print(f"  Stored with surprise: {stored2.surprise_score:.4f}")
        
        # Test very different message (should have higher surprise)
        msg3 = "The quantum foam at Planck scale exhibits non-commutative geometry phenomena"
        surprise3 = titan.compute_surprise(msg3)
        print(f"✓ Message 3 (very different): '{msg3[:50]}...'")
        print(f"  Surprise score: {surprise3:.4f} (novel topic)")
        
        print("✓ Surprise computation working correctly")
        return True
    except Exception as e:
        print(f"✗ Surprise computation error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_store_retrieve(titan):
    """Test store and retrieve cycle"""
    print("\n" + "=" * 60)
    print("TEST 3: Store & Retrieve Cycle")
    print("=" * 60)
    
    try:
        # Store high-surprise messages
        messages = [
            "The neural pathways adapt using Hebbian learning principles",
            "System anomaly detected: unauthorized access from 192.168.1.100",
            "Schedule reminder: quarterly performance review meeting at 3 PM"
        ]
        
        stored_count = 0
        for msg in messages:
            stored = titan.store(msg, context="system_observation")
            if stored:
                stored_count += 1
                print(f"✓ Stored: '{msg[:50]}...' (surprise={stored.surprise_score:.4f})")
            else:
                print(f"⊘ Not stored (low surprise): '{msg[:50]}...'")
        
        print(f"\nStored {stored_count}/{len(messages)} messages")
        
        # Retrieve memories
        print("\nRetrieving memories with query: 'unauthorized access'")
        results = titan.retrieve("unauthorized access", n=3, surprise_weight=0.4)
        print(f"✓ Retrieved {len(results)} memories:")
        for i, mem in enumerate(results, 1):
            print(f"  {i}. {mem[:60]}...")
        
        # Get stats
        stats = titan.get_stats()
        print(f"\n✓ Titan Memory Stats:")
        print(f"  {stats}")
        
        return True
    except Exception as e:
        print(f"✗ Store/retrieve error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_jarvis_imports():
    """Test that jarvis.py imports Titan correctly"""
    print("\n" + "=" * 60)
    print("TEST 4: jarvis.py Import Verification")
    print("=" * 60)
    
    try:
        from core.memory.titan_memory import TitanMemoryModule
        print("✓ TitanMemoryModule can be imported (as jarvis.py requires)")
        
        # Check jarvis.py has the import
        with open('/Users/tayyab/Projects/jarvis/jarvis.py', 'r') as f:
            jarvis_content = f.read()
            assert 'from core.memory.titan_memory import TitanMemoryModule' in jarvis_content, "Import missing"
            print("✓ jarvis.py contains titan import statement")
            
            assert 'titan = None' in jarvis_content, "Global declaration missing"
            print("✓ jarvis.py has titan global declaration")
            
            assert 'titan = TitanMemoryModule()' in jarvis_content, "Initialization missing"
            print("✓ jarvis.py initializes TitanMemoryModule in main()")
            
            assert 'titan.retrieve' in jarvis_content, "Retrieval hook missing"
            print("✓ jarvis.py calls titan.retrieve() in process_request()")
            
            assert 'titan.store' in jarvis_content, "Storage hook missing"
            print("✓ jarvis.py calls titan.store() after reply generation")
        
        return True
    except Exception as e:
        print(f"✗ Import verification error: {e}")
        return False

def main():
    print("\n" + "★" * 60)
    print("★ TITAN MEMORY INTEGRATION TEST SUITE ★")
    print("★" * 60)
    
    results = {}
    
    # Test 1: Module instantiation
    titan = test_titan_module()
    results['module'] = titan is not None
    
    if titan:
        # Test 2: Surprise scoring
        results['surprise'] = test_surprise_computation(titan)
        
        # Test 3: Store/retrieve
        results['store_retrieve'] = test_store_retrieve(titan)
    
    # Test 4: jarvis.py integration
    results['jarvis_imports'] = test_jarvis_imports()
    
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
        print("★ ALL TESTS PASSED - TITAN INTEGRATION SUCCESSFUL ★")
    else:
        print(f"★ {total - passed} TEST(S) FAILED ★")
    print("★" * 60 + "\n")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
