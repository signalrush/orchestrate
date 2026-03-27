# API-First Refactor Design

**Goal:** Make `Orchestrate` a pure HTTP client. Move all Claude Agent SDK calls behind the REST API. Every `orch.run()` goes through the API — self and other agents alike.

## Architecture

```
┌─────────────────────────────────┐
│  Orchestrate (Python client)    │  pip install orchestrate
│  orch.run(), orch.agent()       │  pure HTTP — no SDK dependency
└──────────────┬──────────────────┘
               │ HTTP
┌──────────────▼──────────────────┐
│  REST API (FastAPI)             │  uvicorn api.server:app
│  /agents, /agents/{name}/message│
│  workers, queues, SSE           │
└──────────────┬──────────────────┘
               │ internal
┌──────────────▼──────────────────┐
│  Claude Agent SDK               │  implementation detail
│  ClaudeAgentOptions, query()    │  only imported in server.py
└─────────────────────────────────┘
```

## REST API

### `POST /agents`
Register an agent with config.

**Body:**
```json
{
  "name": "coder",
  "cwd": "/project",
  "model": "claude-opus-4-6",
  "tools": ["Read", "Edit", "Write", "Bash"],
  "prompt": "You are a coder."
}
```

All fields optional except `name`. Defaults:
- `cwd`: server's cwd
- `model`: `claude-opus-4-6`
- `tools`: all tools (`Read`, `Edit`, `Write`, `Bash`, `Glob`, `Grep`, `Agent`, `WebSearch`, `WebFetch`, `Skill`)
- `prompt`: none

**Response:** `{"name": "coder", "status": "registered"}`

### `POST /agents/{name}/message`
Send a message to an agent. Blocks until the agent responds.

**Body (form):**
```
message=implement feature X
source=user|remind|run
schema={"key": "type"}        # optional, for structured responses
```

**Response:**
```json
{"content": "Done! Here's what I changed...", "status": "ok"}
```

Server internally:
1. Looks up agent config by `name`
2. Gets or creates session (one per agent name)
3. Pushes to agent's queue
4. Worker picks up, builds `ClaudeAgentOptions` from config, calls `query()`
5. Emits SSE events during processing
6. Resolves future with response text
7. If `schema` provided and response isn't valid JSON, retries up to 3 times

### `GET /agents/{name}/events`
SSE stream of all events for this agent. Used by UI to show real-time activity.

**Events:**
```
{"event": "MessageQueued", "content": "...", "source": "run"}
{"event": "MessageDequeued", "content": "...", "source": "run"}
{"event": "RunContent", "content": "...", "source": "user"}     # source marker
{"event": "RunContent", "content": "accumulated text"}           # response text
{"event": "ToolCallStarted", "tools": [...]}
```

### `GET /agents`
List all registered agents.

### `DELETE /agents/{name}`
Remove agent and its session/worker/queue.

## Server internals

Per agent:
- **Config** — `{name, cwd, model, tools, prompt}` stored in `AGENTS` dict
- **Session ID** — for `resume` parameter, auto-created on first message
- **Queue** — `asyncio.Queue`, sequential FIFO
- **Worker** — `asyncio.Task`, pulls from queue, calls SDK, emits events
- **SSE channel** — `asyncio.Queue`, worker pushes events, stream reads them

The "self" agent is pre-registered when the program connects. Its config comes from the server's default agent (orchestrator).

### Worker logic (unchanged from current)

```python
async def _session_worker(agent_name):
    while True:
        item = await queue.get()
        if item.get("type") == "done":
            continue
        _emit(agent_name, source_marker)
        _emit(agent_name, dequeue_event)
        response = await _process_message(item, agent_config)
        if item_future:
            item_future.set_result(response)
```

### `_process_message` reads agent config

```python
async def _process_message(message, source, agent_name, session_id, config):
    async for msg in query(
        prompt=message,
        options=ClaudeAgentOptions(
            model=config.get("model", "claude-opus-4-6"),
            cwd=config.get("cwd"),
            system_prompt=config.get("prompt"),
            allowed_tools=config.get("tools", ALL_TOOLS),
            resume=session_id,
            ...
        ),
    ):
        # emit events
```

## Orchestrate client

```python
class Orchestrate:
    """Pure HTTP client. No SDK dependency."""

    def __init__(self, api_url):
        self.api_url = api_url

    def agent(self, name, cwd=None, model=None, tools=None, prompt=None):
        """Register an agent via API."""
        data = {"name": name}
        if cwd: data["cwd"] = cwd
        if model: data["model"] = model
        if tools: data["tools"] = tools
        if prompt: data["prompt"] = prompt
        self._post("/agents", json=data)

    async def run(self, instruction, to="self", schema=None):
        """Send instruction to an agent. Blocks until response."""
        form = {"message": instruction, "source": "run"}
        if schema:
            form["schema"] = json.dumps(schema)
        response = self._post(f"/agents/{to}/message", form=form)
        result_text = response.get("content", "")
        if schema:
            return _parse_json(result_text, schema)
        return result_text

    def _post(self, path, json=None, form=None):
        # urllib.request — no external dependencies
        ...
```

### Retry logic for schema

Stays in the client (`Orchestrate.run`). If `schema` is provided and `_parse_json` fails, retry up to 3 times with a stricter prompt. Each retry is another `POST /agents/{to}/message`.

## CLI changes

`_exec_program` only passes `ORCHESTRATE_API_URL`. No more `ORCHESTRATE_SESSION_ID` — the server manages sessions by agent name.

```python
env = {
    "ORCHESTRATE_API_URL": "http://localhost:7777",
}
```

`_LazyOrchestrate` creates `Orchestrate(api_url=...)` — no session ID needed.

The "self" agent is pre-registered by the server when `POST /agents/{agent_id}/runs` is called. The program's `orch.run("do X")` posts to `POST /agents/self/message`.

## Migration

- `orch.run()`, `orch.agent()` — unchanged API, programs don't break
- `remind()`, `task()` — still work as deprecated aliases
- Server endpoints — `/sessions/{id}/message` still works (backwards compat), but new path is `/agents/{name}/message`
- UI — unchanged, same SSE events

## What changes

| File | Before | After |
|------|--------|-------|
| `core.py` | ~180 lines, SDK imports, direct `query()` calls | ~60 lines, pure HTTP client |
| `server.py` | Handles "self" only, keyed by session_id | Handles all agents, keyed by agent name |
| `cli.py` | Passes `ORCHESTRATE_API_URL` + `ORCHESTRATE_SESSION_ID` | Passes only `ORCHESTRATE_API_URL` |
| pip package | Depends on `claude_agent_sdk` | No SDK dependency |

## What doesn't change

- `orch.run()`, `orch.agent()` — same API
- Skill documentation — same patterns
- UI — same SSE events, same bubbles
- Queue/worker model — same sequential FIFO
- Program patterns — same
