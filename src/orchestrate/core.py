"""orchestrate core — Orchestrate class and helpers."""

import json
import re
import urllib.request
from typing import Any


def _parse_json(text: str, schema: dict) -> dict:
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
    """Orchestrate multiple Claude agents via the REST API."""

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url

    def agent(self, name: str, cwd: str | None = None, model: str | None = None,
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
        self._post_json("/agents", config)

    def run(self, instruction: str, to: str = "self", schema: dict | None = None) -> str | dict:
        """Send instruction to a named agent via POST /agents/{to}/message."""
        max_attempts = 3 if schema else 1
        last_error = None

        for attempt in range(max_attempts):
            prompt = instruction
            if schema and attempt > 0:
                schema_desc = json.dumps(schema, indent=2)
                prompt += (f"\n\nYou MUST respond with ONLY a valid JSON object, no other text. "
                           f"Keys and types:\n{schema_desc}")

            result = self._post_json(f"/agents/{to}/message", {"message": prompt})
            result_text = result.get("content", "") if isinstance(result, dict) else str(result)

            print(f"[{to}] {result_text[:200]}", flush=True)

            if not schema:
                return result_text

            try:
                return _parse_json(result_text, schema)
            except ValueError as e:
                last_error = e
                print(f"[{to}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}", flush=True)

        raise last_error

    def _post_json(self, path: str, data: dict) -> dict:
        """POST JSON to path under api_url and return parsed response."""
        url = f"{self._api_url}{path}"
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run() instead."""
        return self.run(instruction, schema=schema)

    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run(instruction, to=...) instead."""
        return self.run(instruction, to=to, schema=schema)


Auto = Orchestrate  # deprecated alias
