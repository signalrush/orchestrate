"""orchestrate core — Auto class and helpers."""

import json
import os
import re
from typing import Any

try:
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage
except ImportError:
    query = None
    ClaudeAgentOptions = None
    AssistantMessage = None
    ResultMessage = None

ALL_TOOLS = [
    "Read", "Edit", "Write", "Bash", "Glob", "Grep",
    "Agent", "WebSearch", "WebFetch", "Skill",
]


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


class Auto:
    """Orchestrate multiple Claude agents via the Agent SDK.

    Each agent (including 'self') maintains its own session.
    remind() sends to self. task() sends to a named agent.
    """

    def __init__(self, cwd: str | None = None, model: str = "claude-sonnet-4-6",
                 api_url: str | None = None, session_id: str | None = None):
        self._sessions: dict[str, dict] = {}
        self._cwd = cwd or os.getcwd()
        self._model = model
        self._api_url = api_url
        self._session_id = session_id

    def agent(self, name: str, cwd: str | None = None) -> None:
        """Declare a named agent. Optional — task() auto-creates on first use."""
        if name not in self._sessions:
            self._sessions[name] = {
                "session_id": None,
                "cwd": cwd or self._cwd,
            }

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Send instruction to self. Alias for task(instruction, to='self')."""
        return await self.task(instruction, to="self", schema=schema)

    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Send instruction to a named agent. Accumulates session context."""
        # API mode: POST to the API endpoint
        if self._api_url and to == "self":
            return await self._remind_via_api(instruction, schema)

        # SDK mode: direct Agent SDK call (existing code continues below)
        if to not in self._sessions:
            self.agent(to)

        # Append JSON format instructions when schema is provided
        prompt = instruction
        if schema:
            schema_desc = json.dumps(schema, indent=2)
            prompt += f"\n\nRespond with a JSON object with these keys and types:\n{schema_desc}"

        agent = self._sessions[to]
        opts = ClaudeAgentOptions(
            allowed_tools=ALL_TOOLS,
            permission_mode="bypassPermissions",
            cwd=agent["cwd"],
            model=self._model,
            resume=agent["session_id"],
        )

        result_text = ""
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if hasattr(block, "text"):
                        result_text += block.text
            elif isinstance(msg, ResultMessage):
                agent["session_id"] = msg.session_id

        # Log to stdout so orchestrate-run captures output
        print(f"[{to}] {result_text[:200]}", flush=True)

        if schema:
            return _parse_json(result_text, schema)
        return result_text

    async def _remind_via_api(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Send remind via HTTP POST to the API server."""
        import urllib.request
        import urllib.parse

        prompt = instruction
        if schema:
            schema_desc = json.dumps(schema, indent=2)
            prompt += f"\n\nRespond with a JSON object with these keys and types:\n{schema_desc}"

        data = urllib.parse.urlencode({
            "message": prompt,
            "stream": "false",
            "session_id": self._session_id or "",
            "source": "remind",
        }).encode()

        req = urllib.request.Request(
            f"{self._api_url}/agents/orchestrator/runs",
            data=data,
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()

        # Remind endpoint returns JSON: {"content": "...", "status": "ok"}
        result_text = ""
        try:
            result = json.loads(body)
            result_text = result.get("content", "")
        except json.JSONDecodeError:
            result_text = body

        print(f"[remind] {result_text[:200]}", flush=True)

        if schema:
            return _parse_json(result_text, schema)
        return result_text
