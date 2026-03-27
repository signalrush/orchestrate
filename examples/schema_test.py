"""Test schema parsing with real SDK calls."""
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()

    r = await auto.remind(
        "What is the capital of France? What is its population in millions?",
        schema={"city": "str", "population_millions": "float"}
    )
    print(f"Result: {r}")
    assert isinstance(r, dict), f"Expected dict, got {type(r)}"
    assert "city" in r, f"Missing 'city' key: {r}"
    assert "population_millions" in r, f"Missing 'population_millions' key: {r}"
    print("PASS")

asyncio.run(main())
