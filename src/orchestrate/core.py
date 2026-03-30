"""orchestrate core — Orchestrate class (pure HTTP client)."""

import datetime
import json
import re
import uuid
from pathlib import Path
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
        if expected and not isinstance(value, expected):
            errors.append(f"'{key}' expected {type_spec}, got {type(value).__name__}")
    if errors:
        raise ValueError(f"Schema validation failed: {', '.join(errors)}")


class ContextResult(dict):
    """Result of orch.run() — dict subclass. str() returns summary."""

    def __init__(self, id, summary, text, data, agent, task, file):
        super().__init__(data if data else {})
        self.id = id
        self.summary = summary
        self.text = text
        self.data = data
        self.agent = agent
        self.task = task
        self.file = file

    def __str__(self):
        return self.summary

    def __repr__(self):
        return f"ContextResult(id={self.id!r}, agent={self.agent!r}, summary={self.summary!r})"

    def __getattr__(self, name):
        # For non-schema results, delegate string methods to summary
        try:
            summary = self.__dict__["summary"]
            data = self.__dict__["data"]
        except KeyError:
            raise AttributeError(name)
        if data is None:
            return getattr(summary, name)
        raise AttributeError(f"'ContextResult' object has no attribute '{name}'")


class Orchestrate:
    """Pure HTTP client for the orchestrate API.

    orch.run(instruction) — talk to self
    orch.run(instruction, to="agent") — talk to another agent
    orch.agent(name, ...) — register an agent
    """

    def __init__(self, api_url: str | None = None, session_id: str | None = None):
        self._api_url = api_url
        self._session_id = session_id
        client_kwargs: dict[str, Any] = {"timeout": None}
        if api_url:
            client_kwargs["base_url"] = api_url
        self._client = httpx.AsyncClient(**client_kwargs)

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
        self, instruction: str, to: str = "self", schema: dict | None = None, context: list | None = None
    ) -> "ContextResult":
        """Send instruction to a named agent via POST /agents/{to}/message."""
        # Build context prefix before retry loop
        base_instruction = instruction
        if context:
            prefix_parts = []
            for entry in context:
                if isinstance(entry, str):
                    entry = await self._fetch_context(entry)
                if entry is not None:
                    prefix_parts.append(
                        f"[Context from {entry.agent} (full output: {entry.file})]:\n{entry.summary}"
                    )
            if prefix_parts:
                base_instruction = "\n\n".join(prefix_parts) + "\n\n" + instruction

        max_attempts = 3 if schema else 1
        last_error = None
        result_text = ""
        parsed: dict | None = None

        for attempt in range(max_attempts):
            prompt = base_instruction
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
                break

            try:
                parsed = _parse_json(result_text)
                _validate_schema(parsed, schema)
                break
            except ValueError as e:
                last_error = e
                print(
                    f"[{to}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}",
                    flush=True,
                )

        if schema and parsed is None:
            if last_error is not None:
                raise last_error
            raise ValueError("Schema parsing failed after all attempts")

        # Auto-save to context store and write .md file
        entry_id = str(uuid.uuid4())
        summary = result_text[:120]
        file_path = str(Path.home() / ".orchestrate" / "context" / f"{entry_id}.md")

        if self._api_url:
            try:
                save_resp = await self._client.post(
                    "/context",
                    json={
                        "text": result_text,
                        "agent": to,
                        "tags": [],
                    },
                )
                if save_resp.status_code == 200:
                    save_data = save_resp.json()
                    entry_id = str(save_data.get("id", entry_id))
                    summary = save_data.get("summary", summary)
                    file_path = str(Path.home() / ".orchestrate" / "context" / f"{entry_id}.md")
            except Exception:
                pass

        # Write .md file
        try:
            ctx_dir = Path.home() / ".orchestrate" / "context"
            ctx_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().isoformat()
            data_json = json.dumps(parsed, indent=2) if parsed else "null"
            md_content = (
                f"# Context: {to} — {instruction[:60]}\n\n"
                f"**Agent**: {to}\n"
                f"**Created**: {timestamp}\n"
                f"**Schema**: {json.dumps(schema) if schema else 'none'}\n\n"
                f"## Summary\n{summary}\n\n"
                f"## Full Output\n{result_text}\n\n"
                f"## Structured Data\n```json\n{data_json}\n```\n"
            )
            Path(file_path).write_text(md_content)
        except Exception:
            pass

        return ContextResult(
            id=entry_id,
            summary=summary,
            text=result_text,
            data=parsed,
            agent=to,
            task=instruction,
            file=file_path,
        )

    async def _fetch_context(self, entry_id: str) -> "ContextResult | None":
        """Fetch a single context entry by ID from GET /context/{entry_id}."""
        if not self._api_url:
            return None
        try:
            resp = await self._client.get(f"/context/{entry_id}")
            if resp.status_code != 200:
                return None
            row = resp.json()
            file_path = str(Path.home() / ".orchestrate" / "context" / f"{entry_id}.md")
            return ContextResult(
                id=str(row.get("id", entry_id)),
                summary=row.get("summary") or row.get("text", "")[:120],
                text=row.get("text", ""),
                data=None,
                agent=row.get("agent", ""),
                task="",
                file=file_path,
            )
        except Exception:
            return None

    async def recall(self, q="", tags="", agent="", limit=50) -> list["ContextResult"]:
        """Search context store. Returns list of ContextResult objects."""
        if not self._api_url:
            return []
        try:
            resp = await self._client.get(
                "/context",
                params={"q": q, "tags": tags, "agent": agent, "limit": limit},
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            results = []
            for row in rows:
                entry_id = str(row.get("id", ""))
                file_path = str(Path.home() / ".orchestrate" / "context" / f"{entry_id}.md")
                results.append(ContextResult(
                    id=entry_id,
                    summary=row.get("summary") or row.get("text", "")[:120],
                    text=row.get("text", ""),
                    data=None,
                    agent=row.get("agent", ""),
                    task="",
                    file=file_path,
                ))
            return results
        except Exception:
            return []

    async def pin(self, entry: "ContextResult | str") -> None:
        """Pin a context entry. Accepts ContextResult or str ID."""
        entry_id = entry.id if isinstance(entry, ContextResult) else entry
        if self._api_url:
            await self._client.post(f"/context/{entry_id}/pin")

    async def unpin(self, entry: "ContextResult | str") -> None:
        """Unpin a context entry. Accepts ContextResult or str ID."""
        entry_id = entry.id if isinstance(entry, ContextResult) else entry
        if self._api_url:
            await self._client.delete(f"/context/{entry_id}/pin")

    async def remind(self, instruction: str, schema: dict | None = None) -> "ContextResult":
        """Deprecated. Use run() instead."""
        return await self.run(instruction, schema=schema)

    async def task(
        self, instruction: str, to: str, schema: dict | None = None
    ) -> "ContextResult":
        """Deprecated. Use run(instruction, to=...) instead."""
        return await self.run(instruction, to=to, schema=schema)


Auto = Orchestrate  # deprecated alias
