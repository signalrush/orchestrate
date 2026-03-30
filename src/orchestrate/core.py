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

    # Try 3: find { ... } substrings, try each one
    pos = 0
    while pos < len(text):
        brace_start = text.find("{", pos)
        if brace_start < 0:
            break
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
        pos = brace_start + 1

    raise ValueError(f"No valid JSON found in response: {text[:200]}")


_TYPE_MAP = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": (int, float),
    "number": (int, float),
    "bool": bool,
    "boolean": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
}


def _validate_schema(data: dict, schema: dict) -> None:
    """Validate that data has all schema keys with correct types.

    Schema format: {"key": "type_name"} where type_name is one of:
    str, int, float, bool, list, dict (or aliases like string, number, etc.)
    Types containing "|" (e.g., "str | null") accept None as valid.
    """
    errors = []
    for key, type_spec in schema.items():
        if key not in data:
            errors.append(f"missing key '{key}'")
            continue
        value = data[key]
        # Handle nullable types like "str | null"
        if "|" in str(type_spec):
            if value is None:
                continue
            type_spec = type_spec.split("|")[0].strip()
        expected = _TYPE_MAP.get(type_spec.strip().lower())
        if expected and not isinstance(value, expected):  # type: ignore[arg-type]
            errors.append(f"'{key}' expected {type_spec}, got {type(value).__name__}")
    if errors:
        raise ValueError(f"Schema validation failed: {', '.join(errors)}")


class Orchestrate:
    """Pure HTTP client for the orchestrate API.

    orch.run(instruction) — talk to self
    orch.run(instruction, to="agent") — talk to another agent
    orch.agent(name, ...) — register an agent
    """

    def __init__(self, api_url: str | None = None):
        self._api_url = api_url
        self._client = httpx.AsyncClient(
            base_url=api_url or "http://localhost:7777", timeout=None
        )

    async def agent(
        self,
        name: str,
        cwd: str | None = None,
        model: str | None = None,
        tools: list | None = None,
        prompt: str | None = None,
    ) -> None:
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def run(
        self, instruction: str, to: str = "self", schema: dict | None = None
    ) -> str | dict:
        """Send instruction to a named agent via POST /agents/{to}/message."""
        max_attempts = 3 if schema else 1
        last_error = None

        for attempt in range(max_attempts):
            prompt = instruction
            if schema:
                schema_desc = json.dumps(schema, indent=2)
                if attempt == 0:
                    prompt += f"\n\nRespond with a JSON object with these keys and types:\n{schema_desc}"
                else:
                    prompt += (
                        f"\n\nYou MUST respond with ONLY a valid JSON object, no other text. "
                        f"Keys and types:\n{schema_desc}"
                    )

            resp = await self._client.post(
                f"/agents/{to}/message",
                data={"message": prompt, "source": "remind"},
            )
            resp.raise_for_status()
            result = resp.json()
            result_text = (
                result.get("content", "") if isinstance(result, dict) else str(result)
            )

            print(f"[{to}] {result_text[:200]}", flush=True)

            if not schema:
                return result_text

            try:
                parsed = _parse_json(result_text)
                _validate_schema(parsed, schema)
                return parsed
            except ValueError as e:
                last_error = e
                print(
                    f"[{to}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}",
                    flush=True,
                )

        if last_error is not None:
            raise last_error
        raise ValueError("Schema parsing failed after all attempts")

    async def run_task(
        self, task: str, to: str, context: list[str] | None = None
    ) -> dict:
        """POST /agents/{to}/runs — ephemeral task execution."""
        body: dict[str, Any] = {"task": task}
        if context:
            body["context"] = context
        resp = await self._client.post(f"/agents/{to}/runs", json=body)
        resp.raise_for_status()
        return resp.json()

    async def save_context(self, text: str, tags: list[str] | None = None) -> dict:
        """POST /context — save a context entry."""
        body: dict[str, Any] = {"text": text}
        if tags:
            body["tags"] = tags
        resp = await self._client.post("/context", json=body)
        resp.raise_for_status()
        return resp.json()

    async def recall_context(
        self,
        q: str | None = None,
        tags: str | None = None,
        agent: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """GET /context — search context entries."""
        params: dict[str, Any] = {"limit": limit}
        if q:
            params["q"] = q
        if tags:
            params["tags"] = tags
        if agent:
            params["agent"] = agent
        resp = await self._client.get("/context", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run() instead."""
        return await self.run(instruction, schema=schema)

    async def task(
        self, instruction: str, to: str, schema: dict | None = None
    ) -> str | dict:
        """Deprecated. Use run(instruction, to=...) instead."""
        return await self.run(instruction, to=to, schema=schema)


Auto = Orchestrate  # deprecated alias
