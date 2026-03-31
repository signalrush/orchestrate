"""orchestrate core — Orchestrate class (pure HTTP client)."""

import datetime
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

_STOP_WORDS = frozenset(
    "a an the and or but if in on at to of for with by from as is are was were be been "
    "have has had do does did will would could should may might shall can not no nor so "
    "it its this that these those i you he she we they me him her us them my your his "
    "our their what which who whom when where why how all any some each every than then "
    "also just only about above after before between through during while though although "
    "because since until unless whether need use used using via per than make makes made "
    "get gets got give gives given take takes took set sets let lets run runs ran".split()
)


def _extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    """Extract significant keywords from text for context search.

    Strips stop words and short tokens; returns up to max_keywords unique words.
    """
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    seen: dict[str, int] = {}
    for w in words:
        if w not in _STOP_WORDS:
            seen[w] = seen.get(w, 0) + 1
    # Sort by frequency descending, then alphabetically for stability
    ranked = sorted(seen, key=lambda w: (-seen[w], w))
    return ranked[:max_keywords]


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


async def _auto_recall_context(recall_fn, instruction: str) -> "list | None":
    """Auto-inject context by keyword extraction and recall scoring."""
    try:
        keywords = _extract_keywords(instruction)
        if keywords:
            scores: dict[str, int] = {}
            entries: dict[str, "ContextResult"] = {}
            for kw in keywords:
                for entry in await recall_fn(q=kw, limit=10):
                    scores[entry.id] = scores.get(entry.id, 0) + 1
                    entries[entry.id] = entry
            if scores:
                top_ids = sorted(scores, key=lambda eid: -scores[eid])[:3]
                return [entries[eid] for eid in top_ids]
    except Exception:
        pass
    return None


async def _build_context_prefix(
    instruction: str,
    context: "list | None",
    get_context_fn,
) -> str:
    """Build instruction with context prefix prepended."""
    if not context:
        return instruction
    prefix_parts = []
    for entry in context:
        if isinstance(entry, str):
            entry = await get_context_fn(entry)
        if entry is not None:
            prefix_parts.append(
                f"[Context from {entry.agent} (full output: {entry.file})]:\n{entry.summary}"
            )
    if prefix_parts:
        return "\n\n".join(prefix_parts) + "\n\n" + instruction
    return instruction


async def _run_with_schema_retry(
    client: "httpx.AsyncClient",
    agent_name: str,
    base_instruction: str,
    schema: "dict | None",
    title: str = "",
) -> "tuple[str, dict | None]":
    """POST to /agents/{agent_name}/message with optional schema retry (3 attempts).

    Returns (result_text, parsed) where parsed is None when schema is None.
    """
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

        form_data = {"message": prompt, "source": "system"}
        if title:
            form_data["title"] = title
        resp = await client.post(
            f"/agents/{agent_name}/message",
            data=form_data,
        )
        resp.raise_for_status()
        result = resp.json()
        result_text = (
            result.get("content", "") if isinstance(result, dict) else str(result)
        )

        print(f"[{agent_name}] {result_text[:200]}", flush=True)

        if not schema:
            break

        try:
            parsed = _parse_json(result_text)
            _validate_schema(parsed, schema)
            break
        except ValueError as e:
            last_error = e
            print(
                f"[{agent_name}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}",
                flush=True,
            )

    if schema and parsed is None:
        if last_error is not None:
            raise last_error
        raise ValueError("Schema parsing failed after all attempts")

    return result_text, parsed


async def _save_and_return(
    client: "httpx.AsyncClient | None",
    agent_name: str,
    instruction: str,
    result_text: str,
    parsed: "dict | None",
    schema: "dict | None",
) -> "ContextResult":
    """Auto-save result to context store, write .md file, return ContextResult."""
    entry_id = str(uuid.uuid4())
    summary = result_text[:120]
    file_path = str(Path.home() / ".orchestrate" / "context" / f"{entry_id}.md")

    run_id = str(uuid.uuid4())
    if client is not None:
        try:
            save_resp = await client.post(
                "/context",
                json={
                    "text": result_text,
                    "agent": agent_name,
                    "tags": [],
                    "run_id": run_id,
                },
            )
            if save_resp.status_code == 200:
                save_data = save_resp.json()
                entry_id = str(save_data.get("id", entry_id))
                summary = save_data.get("summary", summary)
                file_path = str(Path.home() / ".orchestrate" / "context" / f"{entry_id}.md")
            else:
                print(f"[orchestrate] warning: POST /context returned {save_resp.status_code}", flush=True)
        except Exception as e:
            print(f"[orchestrate] warning: failed to save context: {e}", flush=True)

    # Write .md file
    try:
        ctx_dir = Path.home() / ".orchestrate" / "context"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat()
        data_json = json.dumps(parsed, indent=2) if parsed else "null"
        md_content = (
            f"# Context: {agent_name} — {instruction[:60]}\n\n"
            f"**Agent**: {agent_name}\n"
            f"**Created**: {timestamp}\n"
            f"**Schema**: {json.dumps(schema) if schema else 'none'}\n\n"
            f"## Summary\n{summary}\n\n"
            f"## Full Output\n{result_text}\n\n"
            f"## Structured Data\n```json\n{data_json}\n```\n"
        )
        Path(file_path).write_text(md_content)
    except Exception as e:
        print(f"[orchestrate] warning: failed to write context file: {e}", flush=True)

    return ContextResult(
        id=entry_id,
        summary=summary,
        text=result_text,
        data=parsed,
        agent=agent_name,
        task=instruction,
        file=file_path,
    )


def _parse_agent_file(path: Path) -> "tuple[dict, str]":
    """Parse YAML frontmatter and body from an agent .md file.

    Returns (frontmatter_dict, body_str). If no frontmatter, returns ({}, full_text).
    """
    text = path.read_text()
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    fm_text = text[3:end].strip()
    body = text[end + 4:].strip()

    # Minimal YAML parsing: key: value pairs only
    fm: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()

    return fm, body


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

    async def subagent(
        self,
        instruction: str,
        to: str,
        parent_context: "list[ContextResult] | ContextResult | None" = None,
        schema: dict | None = None,
        no_context: bool = False,
    ) -> "ContextResult":
        """Spawn a child agent that inherits parent context.

        Pass parent_context to explicitly inject results from a parent agent.
        Without parent_context, auto-context injection applies normally unless
        no_context=True.
        """
        ctx: list | None = None
        if parent_context is not None:
            ctx = [parent_context] if isinstance(parent_context, ContextResult) else list(parent_context)
        return await self.run(instruction, to=to, schema=schema, context=ctx, no_context=no_context)

    async def run(
        self,
        instruction: str,
        to: str = "self",
        schema: dict | None = None,
        context: list | None = None,
        no_context: bool = False,
    ) -> "ContextResult":
        """Send instruction to a named agent via POST /agents/{to}/message.

        Args:
            instruction: The task/prompt for the agent.
            to: Agent name to target (default "self" → "orchestrator").
            schema: Optional dict schema for structured JSON output.
            context: Explicit list of ContextResult or str IDs to prepend.
                     When None and no_context is False, auto-context recall
                     is attempted (requires api_url to be set).
            no_context: Set True to disable auto-context injection for this call.
        """
        # Auto-inject context when not explicitly provided and not disabled.
        if context is None and not no_context and self._api_url:
            context = await _auto_recall_context(self.recall, instruction)

        # Build context prefix before retry loop
        base_instruction = await _build_context_prefix(instruction, context, self.get_context)

        result_text, parsed = await _run_with_schema_retry(
            client=self._client,
            agent_name=to,
            base_instruction=base_instruction,
            schema=schema,
            title=instruction[:80],
        )

        return await _save_and_return(
            client=self._client if self._api_url else None,
            agent_name=to,
            instruction=instruction,
            result_text=result_text,
            parsed=parsed,
            schema=schema,
        )

    async def get_context(self, entry_id: str) -> "ContextResult | None":
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
            resp = await self._client.post(f"/context/{entry_id}/pin")
            if resp.status_code == 404:
                print(f"[orchestrate] warning: context entry {entry_id} not found for pin", flush=True)
            else:
                resp.raise_for_status()

    async def unpin(self, entry: "ContextResult | str") -> None:
        """Unpin a context entry. Accepts ContextResult or str ID."""
        entry_id = entry.id if isinstance(entry, ContextResult) else entry
        if self._api_url:
            resp = await self._client.delete(f"/context/{entry_id}/pin")
            if resp.status_code == 404:
                print(f"[orchestrate] warning: context entry {entry_id} not found for unpin", flush=True)
            else:
                resp.raise_for_status()

    async def remind(self, instruction: str, schema: dict | None = None) -> "ContextResult":
        """Deprecated. Use run() instead."""
        return await self.run(instruction, to="self", schema=schema)

    async def task(
        self, instruction: str, to: str, schema: dict | None = None
    ) -> "ContextResult":
        """Deprecated. Use run(instruction, to=...) instead."""
        return await self.run(instruction, to=to, schema=schema)


Auto = Orchestrate  # deprecated alias


class Agent:
    """Agent-centric API for orchestrate.

    Usage:
        agent = Agent("research")  # loads from ~/.claude/agents/research.md
        result = await agent.arun("analyze codebase")
    """

    def __init__(
        self,
        name: str,
        prompt: str | None = None,
        model: str | None = None,
        tools: list | None = None,
        api_url: str | None = None,
    ):
        """Create an agent.

        If ~/.claude/agents/{name}.md exists, loads prompt/tools/model from it.
        Explicit args override the file config.
        api_url defaults to ORCHESTRATE_API_URL env var, then http://localhost:7777.
        """
        self.name = name

        # Resolve api_url
        if api_url is None:
            api_url = os.environ.get("ORCHESTRATE_API_URL", "http://localhost:7777")
        self._api_url = api_url

        # Load agent file config as base
        file_prompt: str | None = None
        file_model: str | None = None
        file_tools: list | None = None

        agent_file = Path.home() / ".claude" / "agents" / f"{name}.md"
        if agent_file.exists():
            fm, body = _parse_agent_file(agent_file)
            file_prompt = body if body else None
            file_model = fm.get("model")
            raw_tools = fm.get("tools")
            if raw_tools:
                file_tools = [t.strip() for t in raw_tools.split(",") if t.strip()]

        # Explicit args override file config
        self.prompt = prompt if prompt is not None else file_prompt
        self.model = model if model is not None else file_model
        self.tools = tools if tools is not None else file_tools

        self._client = httpx.AsyncClient(base_url=self._api_url, timeout=None)
        self._registered = False

    async def _ensure_registered(self) -> None:
        """Register with server on first arun() call."""
        if self._registered:
            return
        config: dict[str, Any] = {"name": self.name}
        if self.prompt is not None:
            config["prompt"] = self.prompt
        if self.model is not None:
            config["model"] = self.model
        if self.tools is not None:
            config["tools"] = self.tools
        import os as _os
        cwd = _os.getcwd()
        config["cwd"] = cwd
        resp = await self._client.post("/agents", json=config)
        resp.raise_for_status()
        self._registered = True

    async def arun(
        self,
        instruction: str,
        context: list | None = None,
        schema: dict | None = None,
    ) -> "ContextResult":
        """Send instruction to this agent. Returns ContextResult.

        context: explicit list of ContextResult or str entry IDs.
        schema: dict for structured JSON output with retry.
        """
        await self._ensure_registered()

        # Build context prefix (using a no-op get_context_fn for str IDs since
        # we have direct client access)
        async def _get_context(entry_id: str) -> "ContextResult | None":
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

        base_instruction = await _build_context_prefix(instruction, context, _get_context)

        result_text, parsed = await _run_with_schema_retry(
            client=self._client,
            agent_name=self.name,
            base_instruction=base_instruction,
            schema=schema,
            title=instruction[:80],
        )

        return await _save_and_return(
            client=self._client,
            agent_name=self.name,
            instruction=instruction,
            result_text=result_text,
            parsed=parsed,
            schema=schema,
        )

    def spawn(self, name: str, **overrides) -> "Agent":
        """Spawn a child agent inheriting this agent's config."""
        return Agent(
            name=name,
            prompt=overrides.get("prompt", self.prompt),
            model=overrides.get("model", self.model),
            tools=overrides.get("tools", self.tools),
            api_url=overrides.get("api_url", self._api_url),
        )

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
