# orchestrate — Agent SDK-based orchestration library

## Overview

A thin Python wrapper over the Claude Agent SDK that lets programs coordinate multiple Claude agents through a simple `remind()`/`task()` API. Replaces the stop-hook IPC approach used by `auto` with direct in-process SDK calls.

## Problem

The current `auto` framework uses a fragile architecture: a Python program communicates with Claude Code via file-based IPC (JSON state files) and a bash stop hook that intercepts turn endings. This causes path mismatches, race conditions, transcript extraction failures, and session registration bugs. The `task()` dispatch uses `claude -p` subprocesses with cold starts and no shared context.

## Solution

Replace the entire IPC layer with the Claude Agent SDK's `query()` function. Each agent (including "self") is a persistent SDK session. No hooks, no files, no subprocesses. Everything runs in one Python process.

## Core Concepts

- **Agent** — a named SDK session that accumulates context across calls. "self" is just another agent.
- **`remind(instruction)`** — alias for `task(instruction, to="self")`. Sends a message to the self agent.
- **`task(instruction, to="name")`** — sends a message to a named agent's session. The agent executes the instruction with full tool access and returns the result.
- **Session accumulation** — every agent maintains its own session ID. Each call resumes the previous session, so context grows over time. Number of sessions = number of agents.
- **Concurrency** — follows Python's natural async semantics. `await task()` is sequential. `asyncio.gather(task(...), task(...))` is concurrent. No framework magic.

## API

```python
from orchestrate import Auto, state

class Auto:
    def __init__(self, cwd=None, model="claude-sonnet-4-6")
    def agent(self, name, cwd=None)
    async def remind(self, instruction, schema=None) -> str | dict
    async def task(self, instruction, to, schema=None) -> str | dict
```

### `Auto(cwd=None, model="claude-sonnet-4-6")`

Create an orchestrator. All agents default to `cwd` and `model`.

### `auto.agent(name, cwd=None)`

Declare a named agent. Optional — `task()` auto-creates agents on first use.

### `auto.remind(instruction, schema=None)`

Send instruction to the "self" agent. Equivalent to `task(instruction, to="self")`.
- Without schema: returns `str` (Claude's text response).
- With schema: returns `dict` (parsed JSON matching the schema keys).

### `auto.task(instruction, to, schema=None)`

Send instruction to a named agent. Same return semantics as `remind()`.

### `state` module

Persistent key-value store saved to `orchestrate-state.json`. Same API as `auto.state`:
- `state.set(key, value)`
- `state.get(key=None)` — returns value for key, or entire dict if no key.
- `state.update(dict)` — merge keys.

## Architecture

```
orchestrate/
├── orchestrate/
│   ├── __init__.py      # exports Auto, state
│   ├── core.py          # Auto class (~150 lines)
│   └── state.py         # state persistence
├── pyproject.toml
└── README.md
```

### Implementation: `core.py`

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

class Auto:
    def __init__(self, cwd=None, model="claude-sonnet-4-6"):
        self._sessions = {}
        self._cwd = cwd or os.getcwd()
        self._model = model

    def agent(self, name, cwd=None):
        if name not in self._sessions:
            self._sessions[name] = {"session_id": None, "cwd": cwd or self._cwd}

    async def remind(self, instruction, schema=None):
        return await self.task(instruction, to="self", schema=schema)

    async def task(self, instruction, to, schema=None):
        if to not in self._sessions:
            self.agent(to)

        agent = self._sessions[to]
        opts = ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep",
                          "Agent", "WebSearch", "WebFetch", "Skill"],
            permission_mode="bypassPermissions",
            cwd=agent["cwd"],
            model=self._model,
            resume=agent["session_id"],
        )

        result_text = ""
        async for msg in query(prompt=instruction, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if hasattr(block, "text"):
                        result_text += block.text
            elif isinstance(msg, ResultMessage):
                agent["session_id"] = msg.session_id

        if schema:
            return _parse_json(result_text, schema)
        return result_text
```

### Implementation: `state.py`

Copy from `auto/src/auto/state.py`. Change file path from `auto-state.json` to `orchestrate-state.json`. Same atomic write + flock semantics.

### JSON parsing: `_parse_json()`

Lenient JSON extraction from response text:
1. Try `json.loads(text)` directly.
2. Try extracting from markdown fences (` ```json ... ``` `).
3. Try finding first `{...}` substring.
4. Raise `ValueError` if all fail.

No automatic retries in the library. The user program decides what to do on parse failure (retry, default, crash).

## Configuration

- **Model**: passed to `Auto(model=...)`. Default: `claude-sonnet-4-6`.
- **Tools**: all tools enabled. No configuration needed.
- **Permissions**: `bypassPermissions` (yolo mode). No prompts.
- **Auth**: reads `ANTHROPIC_API_KEY` from environment. Works with OAuth tokens (`sk-ant-oat01-...`) and API keys.

## Usage Example

```python
import asyncio
from orchestrate import Auto, state

async def main():
    auto = Auto(cwd="/home/user/project")

    r = await auto.remind("Check setup, report status",
                          schema={"ready": "bool"})

    best = 0
    for i in range(20):
        # Concurrent research
        hive, web = await asyncio.gather(
            auto.task("Check leaderboard", to="scout"),
            auto.task("Search for techniques", to="researcher"),
        )

        # Self acts on research
        r = await auto.remind(
            f"Iteration {i+1}. Research:\n{hive}\n{web}\n"
            "Pick one experiment, implement, eval.",
            schema={"score": "float", "description": "str"}
        )

        if r["score"] > best:
            best = r["score"]
            state.update({"best": best})
            await auto.remind(f"Keep! Score {best}. Log and push.")
        else:
            await auto.remind("Revert. Log as discard.")

asyncio.run(main())
```

## What's NOT in Phase 1

- TUI (Phase 2)
- CLI (`orchestrate-run`, status, stop)
- Streaming callbacks
- Crash recovery / session persistence to disk
- Self-invocation (agents spawning orchestrate programs)

## Dependencies

- `claude-agent-sdk` — the Claude Agent SDK Python package
- Python 3.10+
