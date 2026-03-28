"""orchestrate core — Orchestrate class (pure HTTP client)."""

import json
import re
from typing import Any

import httpx


def _parse_json(text: str) -> dict:
    """Extract JSON object from response text. Lenient parsing.

    Tries in order:
    1. Direct json.loads on stripped text
    2. Extract from markdown ```json ... ``` fences
    3. Find first {...} substring

    Raises ValueError if no valid JSON found.
    """
    text = text.strip()
    if not text:
        raise ValueError("No valid JSON found in empty response")

    # Try 1: direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Try 2: markdown fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Try 3: find first { ... }
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[brace_start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break

    raise ValueError(f"No valid JSON found in response: {text[:200]}")


class Orchestrate:
    """Pure HTTP client for the orchestrate API.

    orch.run(instruction) — talk to self
    orch.run(instruction, to="agent") — talk to another agent
    orch.agent(name, ...) — register an agent
    """

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url
        self._client = httpx.AsyncClient(base_url=api_url, timeout=None)

    async def agent(self, name: str, cwd: str | None = None, model: str | None = None,
                    tools: list | None = None, prompt: str | None = None) -> None:
        """Declare a named agent via POST /agents."""
        config: dict[str, Any] = {"name": name}
        if cwd is not None:
            config["cwd"] = cwd
        if model is not None:
            config["model"] = model
        if tools is not None:
            config["tools"] = tools
        if prompt is not None:
            config["prompt"] = prompt
        resp = await self._client.post("/agents", json=config)
        resp.raise_for_status()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def run(self, instruction: str, to: str = "self", schema: dict | None = None) -> str | dict:
        """Send instruction to a named agent via POST /agents/{to}/message."""
        max_attempts = 3 if schema else 1
        last_error = None

        for attempt in range(max_attempts):
            prompt = instruction
            if schema and attempt > 0:
                schema_desc = json.dumps(schema, indent=2)
                prompt += (f"\n\nYou MUST respond with ONLY a valid JSON object, no other text. "
                           f"Keys and types:\n{schema_desc}")

            resp = await self._client.post(
                f"/agents/{to}/message",
                data={"message": prompt, "source": "remind"},
            )
            resp.raise_for_status()
            result = resp.json()
            result_text = result.get("content", "") if isinstance(result, dict) else str(result)

            print(f"[{to}] {result_text[:200]}", flush=True)

            if not schema:
                return result_text

            try:
                return _parse_json(result_text)
            except ValueError as e:
                last_error = e
                print(f"[{to}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}", flush=True)

        raise last_error

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run() instead."""
        return await self.run(instruction, schema=schema)

    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run(instruction, to=...) instead."""
        return await self.run(instruction, to=to, schema=schema)


Auto = Orchestrate  # deprecated alias
