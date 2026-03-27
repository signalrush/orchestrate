"""End-to-end test using OAuth token from ~/.claude/.credentials.json"""
import asyncio
import json
import os

# Load OAuth token
creds = json.load(open(os.path.expanduser("~/.claude/.credentials.json")))
os.environ["ANTHROPIC_API_KEY"] = creds["claudeAiOauth"]["accessToken"]
print(f"Using OAuth token: {os.environ['ANTHROPIC_API_KEY'][:20]}...")

from orchestrate import Auto, state

async def main():
    auto = Auto(cwd="/home/tianhao/orchestrate")

    # === Test 1: Simple remind ===
    print("\n--- Test 1: Simple remind ---")
    r = await auto.remind("Say exactly: 'hello from orchestrate'")
    print(f"Result: {r.strip()}")
    assert "hello from orchestrate" in r.lower()
    print("PASS")

    # === Test 2: Schema parsing ===
    print("\n--- Test 2: Schema remind ---")
    r = await auto.remind(
        "What is 7 * 8?",
        schema={"answer": "int", "explanation": "str"}
    )
    print(f"Result: {r}")
    assert isinstance(r, dict)
    assert r["answer"] == 56 or r["answer"] == "56"
    print("PASS")

    # === Test 3: Named agent ===
    print("\n--- Test 3: Named agent ---")
    r = await auto.task("What is the square root of 144? Reply with just the number.", to="calculator")
    print(f"Calculator: {r.strip()}")
    assert "12" in r
    print("PASS")

    # === Test 4: Session accumulation (same agent, 2nd call) ===
    print("\n--- Test 4: Session accumulation ---")
    r = await auto.task("What was the last number I asked you about? Reply with just the number.", to="calculator")
    print(f"Calculator (recall): {r.strip()}")
    assert "144" in r or "12" in r  # should remember previous context
    print("PASS")

    # === Test 5: Concurrent agents ===
    print("\n--- Test 5: Concurrent agents ---")
    a, b = await asyncio.gather(
        auto.task("What is 10+10? Reply with just the number.", to="agent_a"),
        auto.task("What is 20+20? Reply with just the number.", to="agent_b"),
    )
    print(f"Agent A: {a.strip()}, Agent B: {b.strip()}")
    assert "20" in a
    assert "40" in b
    print("PASS")

    # === Test 6: State persistence ===
    print("\n--- Test 6: State persistence ---")
    state.set("test_key", "test_value")
    state.update({"iteration": 1, "score": 99.5})
    assert state.get("test_key") == "test_value"
    assert state.get("iteration") == 1
    assert state.get("score") == 99.5
    all_state = state.get()
    assert len(all_state) == 3
    print(f"State: {all_state}")
    print("PASS")

    # === Test 7: Self remind uses agent results ===
    print("\n--- Test 7: Self uses agent results ---")
    research = await auto.task("List 3 prime numbers under 20, comma-separated, nothing else.", to="math_helper")
    result = await auto.remind(
        f"A math helper gave me these primes: {research.strip()}. "
        "What is their sum?",
        schema={"sum": "int"}
    )
    print(f"Primes: {research.strip()}, Sum: {result}")
    assert isinstance(result, dict)
    assert "sum" in result
    print("PASS")

    # === Summary ===
    print(f"\n{'='*50}")
    print(f"ALL 7 TESTS PASSED")
    print(f"Sessions created: {list(auto._sessions.keys())}")
    for name, sess in auto._sessions.items():
        print(f"  {name}: session_id={'set' if sess['session_id'] else 'None'}")

    # Cleanup
    import pathlib
    pathlib.Path("orchestrate-state.json").unlink(missing_ok=True)
    pathlib.Path(".orchestrate-state.lock").unlink(missing_ok=True)

asyncio.run(main())
