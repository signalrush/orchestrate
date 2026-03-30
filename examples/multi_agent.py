"""Smoke test: two agents + remind, session accumulation."""

import asyncio
from orchestrate import Auto


async def main():
    auto = Auto()

    # Task to named agent
    a = await auto.task("What is 2+2? Reply with just the number.", to="math")
    print(f"Agent 'math' said: {a.strip()}")

    # Second task to same agent (should accumulate session)
    b = await auto.task("What is 3+3? Reply with just the number.", to="math")
    print(f"Agent 'math' (turn 2) said: {b.strip()}")

    # Self remind using agent results
    r = await auto.remind(
        f"The math agent computed: {a.strip()} and {b.strip()}. Summarize in one sentence."
    )
    print(f"Self said: {r.strip()}")

    # Verify sessions exist
    assert "math" in auto._sessions, "math agent not created"
    assert "self" in auto._sessions, "self agent not created"
    assert (
        auto._sessions["math"]["session_id"] is not None
    ), "math session not accumulated"
    assert (
        auto._sessions["self"]["session_id"] is not None
    ), "self session not accumulated"

    print("PASS")


asyncio.run(main())
