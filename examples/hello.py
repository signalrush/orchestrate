"""Smoke test: single remind() call."""
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()
    result = await auto.remind("Say 'orchestrate works' and nothing else.")
    print(f"Result: {result}")
    assert "orchestrate works" in result.lower(), f"Unexpected: {result}"
    print("PASS")

asyncio.run(main())
