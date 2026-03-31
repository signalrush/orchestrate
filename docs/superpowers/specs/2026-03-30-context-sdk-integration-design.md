# Orchestrate SDK — Current Architecture Reference

**Date**: 2026-03-30
**Status**: Implemented

---

## Overview

The orchestrate package provides two APIs for multi-agent orchestration:

- **`Agent`** — new primary API. Agent-centric, explicit context, file-based config.
- **`Orchestrate`** — legacy API. HTTP client wrapper, auto-context injection.

```python
from orchestrate import Agent, Orchestrate, ContextResult
```

---

## Agent class

### Creation

```python
# From ~/.claude/agents/{name}.md (YAML frontmatter + body = system prompt)
agent = Agent("research")

# Inline config — no file required
agent = Agent("worker", prompt="You are a code reviewer.", model="claude-sonnet-4-6")

# File config + explicit overrides (explicit wins)
agent = Agent("research", model="claude-opus-4-6")  # overrides model from file
```

`api_url` defaults to `ORCHESTRATE_API_URL` env var, then `http://localhost:7777`.

On first `arun()` call, the agent registers itself via `POST /agents`.

### `arun(instruction, context=None, schema=None)`

```python
result = await agent.arun("Summarise the codebase")

# Explicit context chaining — context= is the only way to pass prior results
r1 = await analyst.arun("Find security issues")
r2 = await fixer.arun("Fix the issues found", context=[r1])

# Parallel execution
r1, r2 = await asyncio.gather(
    agent_a.arun("task A"),
    agent_b.arun("task B"),
)
```

**No auto-context.** Context must be passed explicitly. If `context=None`, no prior results are injected.

Each `arun()` auto-saves its result to `POST /context` and writes `~/.orchestrate/context/{id}.md`.

### `spawn(name, **overrides)`

```python
# Child inherits parent's prompt, model, tools, api_url
child = parent.spawn("child-agent")

# Override specific fields
child = parent.spawn("child-agent", model="claude-haiku-4-5-20251001", prompt="Be brief.")
```

Spawned agents are independent `Agent` instances — they do not share HTTP connections or state.

### Schema support

```python
result = await agent.arun(
    "Analyse the PR",
    schema={"verdict": "str", "confidence": "float", "issues": "list"},
)
# result["verdict"], result["confidence"], result["issues"] — dict access
# result.text — full raw output
```

Schema triggers a 3-attempt retry loop. Each attempt appends progressively stricter JSON instructions to the prompt. Raises `ValueError` after 3 failures. Supported types: `str`, `int`, `float`, `bool`, `list`, `dict` (and aliases). Nullable: `"str | null"`.

### Resource management

```python
async with Agent("worker") as agent:
    result = await agent.arun("...")
# HTTP client closed automatically

# Or manually:
await agent.aclose()
```

---

## ContextResult

Return type of `arun()` and `Orchestrate.run()`. Subclasses `dict`.

| Access | Value |
|---|---|
| `str(result)` | `result.summary` (first 120 chars of output, or server-generated summary) |
| `result["key"]` | Schema field (when `schema=` was used) |
| `result.text` | Full raw agent output |
| `result.id` | Context store entry ID |
| `result.agent` | Agent name |
| `result.task` | Original instruction |
| `result.file` | `~/.orchestrate/context/{id}.md` |
| `result.upper()` etc. | Delegates to `summary` string when `data is None` |

---

## Legacy Orchestrate class

Still works. Use for programs already written against it.

```python
orch = Orchestrate(api_url="http://localhost:7777")

# Register a named agent
await orch.agent("analyst", prompt="You are a senior engineer.")

# Send a message
result = await orch.run("Review the codebase", to="analyst")

# Explicit context
result = await orch.run("Fix issues", to="fixer", context=[prior_result])

# Disable auto-context injection for a clean call
result = await orch.run("Fresh analysis", no_context=True)

# Spawn child with parent context (thin wrapper over run())
child = await orch.subagent("Write tests", to="tester", parent_context=result)

# Context store search
entries = await orch.recall(q="authentication", limit=10)
await orch.pin(entry)
```

**Auto-context**: When `context=None` and `no_context=False` and `api_url` is set, `Orchestrate.run()` extracts keywords from the instruction, queries `recall()` per keyword, and injects the top-3 scoring entries as a prompt prefix. `Agent.arun()` does **not** do this.

Deprecated aliases: `orch.remind()` → `run()`, `orch.task()` → `run()`, `Auto` → `Orchestrate`.

---

## Server-side agent loading

### `~/.claude/agents/` at startup

```
~/.claude/agents/
    research.md          # → AgentDefinition(name="research", ...)
    implementer.md
    reviewer.md
```

Each file: YAML frontmatter (`model`, `tools`, `description`) + body (system prompt).

```markdown
---
model: sonnet
tools: Read,Edit,Bash,Grep,Glob
description: Senior code reviewer
---
You are a senior engineer reviewing pull requests...
```

Loaded at server startup into `AGENT_DEFINITIONS: dict[str, AgentDefinition]`.

### System prompt resolution (per request)

Priority: `config["prompt"]` (POST /agents body) > `AGENT_DEFINITIONS[name].prompt` > `None`

When a system prompt is set:
```python
{"type": "preset", "preset": "claude_code", "append": system_prompt}
```
This appends to Claude Code's default system prompt rather than replacing it.

### `agents=` parameter

Every `query()` call passes `agents=AGENT_DEFINITIONS or None`. This makes all file-defined agent definitions available as subagents the running Claude Code instance can spawn via the `Agent` tool.

### Agent registration

`POST /agents` registers or updates an agent in the SQLite `agents` table. Fields: `name`, `prompt`, `model`, `tools`, `cwd`. Registered agents persist across server restarts.

---

## Context store

Results are saved to SQLite (`~/.orchestrate/orchestrate.db`, `context_entries` table) and mirrored to `~/.orchestrate/context/{id}.md`.

| Endpoint | Purpose |
|---|---|
| `POST /context` | Save entry (called automatically by `_save_and_return`) |
| `GET /context` | Search: `?q=keyword&agent=name&limit=N` |
| `GET /context/{id}` | Fetch single entry |
| `POST /context/{id}/pin` | Pin entry |
| `DELETE /context/{id}/pin` | Unpin entry |
