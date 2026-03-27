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
    run() sends to self or a named agent.
    """

    def __init__(self, cwd: str | None = None, model: str = "claude-sonnet-4-6",
                 api_url: str | None = None, session_id: str | None = None,
                 program_name: str | None = None):
        self._sessions: dict[str, dict] = {}
        self._cwd = cwd or os.getcwd()
        self._model = model
        self._api_url = api_url
        self._session_id = session_id
        self._team_id: str | None = None
        self._program_name = program_name

        if self._api_url and self._session_id:
            self._register_team()

    def _register_team(self):
        """Register this program as a team via the API."""
        import urllib.request
        data = json.dumps({
            "id": self._program_name or self._session_id,
            "name": self._program_name or "program",
            "session_id": self._session_id,
            "model": {"name": self._model, "model": self._model, "provider": "anthropic"},
        }).encode()
        req = urllib.request.Request(
            f"{self._api_url}/teams",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                self._team_id = result.get("id")
        except Exception:
            pass

    def _register_member(self, name: str):
        """Register a named agent as a team member via the API."""
        import urllib.request
        data = json.dumps({"name": name}).encode()
        req = urllib.request.Request(
            f"{self._api_url}/teams/{self._team_id}/members",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                self._sessions[name]["api_session_id"] = result.get("session_id")
        except Exception:
            pass

    def agent(self, name: str, cwd: str | None = None) -> None:
        """Declare a named agent. Optional — run() auto-creates on first use."""
        if name not in self._sessions:
            self._sessions[name] = {
                "session_id": None,
                "cwd": cwd or self._cwd,
            }
            if self._api_url and self._team_id:
                self._register_member(name)

    async def run(self, instruction: str, to: str = "self", schema: dict | None = None) -> str | dict:
        """Send instruction to a named agent. Defaults to self. Accumulates session context."""
        # API mode: route through API
        if self._api_url:
            if to == "self":
                return await self._remind_via_api(instruction, schema)
            # Sub-agent: route through member's API session
            api_sid = self._sessions.get(to, {}).get("api_session_id")
            if api_sid:
                return await self._remind_via_api(instruction, schema, session_id=api_sid, source=to)

        # SDK mode: direct Agent SDK call
        if to not in self._sessions:
            self.agent(to)

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

        print(f"[{to}] {result_text[:200]}", flush=True)

        if schema:
            return _parse_json(result_text, schema)
        return result_text

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run() instead."""
        return await self.run(instruction, schema=schema)

    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run(instruction, to=...) instead."""
        return await self.run(instruction, to=to, schema=schema)

    async def _remind_via_api(self, instruction: str, schema: dict | None = None,
                              session_id: str | None = None, source: str = "remind") -> str | dict:
        """Send message via HTTP POST to the session message endpoint."""
        import urllib.request
        import urllib.parse

        target_session = session_id or self._session_id
        max_attempts = 3 if schema else 1
        last_error = None

        for attempt in range(max_attempts):
            prompt = instruction
            if schema and attempt > 0:
                schema_desc = json.dumps(schema, indent=2)
                prompt += (f"\n\nYou MUST respond with ONLY a valid JSON object, no other text. "
                           f"Keys and types:\n{schema_desc}")

            form_fields = {
                "message": prompt,
                "source": source,
            }

            data = urllib.parse.urlencode(form_fields).encode()

            req = urllib.request.Request(
                f"{self._api_url}/sessions/{target_session}/message",
                data=data,
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=300) as resp:
                body = resp.read().decode()

            result_text = ""
            try:
                result = json.loads(body)
                result_text = result.get("content", "")
            except json.JSONDecodeError:
                result_text = body

            print(f"[{source}] {result_text[:200]}", flush=True)

            if not schema:
                return result_text

            try:
                return _parse_json(result_text, schema)
            except ValueError as e:
                last_error = e
                print(f"[{source}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}", flush=True)

        raise last_error
