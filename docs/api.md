# Orchestrate REST API

Base URL: `http://localhost:7777`

## Architecture

```
Browser ←── SSE stream ←── TEAM_SSE queue ←── Worker emits events
                                                    ↑
POST /agents/{name}/sessions ──→ AGENT_QUEUES ──→ Worker pulls & processes
POST /agents/{name}/message  ──→ AGENT_QUEUES    (sequential, FIFO)
POST /agents/{name}/runs     ──→ ephemeral inline execution (no queue)
```

The core primitive is an **agent**, identified by name. The server manages one session per agent internally. Each agent has:

- **Queue** (`AGENT_QUEUES`): input — all messages go here
- **Worker** (`AGENT_WORKERS`): background task — processes queue items one by one
- **SSE channel** (`AGENT_SSE`): output — worker pushes events, stream reads them

The orchestrate client is pure HTTP — no SDK dependency.

## Endpoints

### `GET /health`
Health check.

**Response:** `{"status": "ok"}`

---

### `GET /agents`
List registered agents.

**Response:**
```json
[{"id": "orchestrator", "name": "orchestrator", "model": "claude-opus-4-6", "cwd": "/path", "tools": [...], "prompt": ""}]
```

---

### `POST /agents`
Register a new agent. Also calls `_ensure_agent_worker` to start the worker immediately and emits an `AgentRegistered` event to the orchestrator's SSE stream so the UI sidebar refreshes.

**Body (JSON):**
```json
{
  "name": "my-agent",
  "model": "claude-sonnet-4-6",
  "cwd": "/path/to/workdir",
  "tools": ["Read", "Edit", "Bash"],
  "prompt": "optional system prompt"
}
```

If `name` is omitted a UUID is generated. All fields except `name` are optional and default to server values.

**Response:** The registered agent object.

---

### `DELETE /agents/{name}`
Remove an agent and clean up its queue, worker, and SSE channel.

**Response:** `{"status": "deleted"}`

---

### `POST /agents/{name}/message`
Push a message to the agent's queue. **Blocks until the worker processes it and returns the response.**

Used by orchestrate programs and any HTTP client that needs a synchronous reply.

`"self"` is an alias for `"orchestrator"` — sending to `/agents/self/message` routes to the orchestrator agent.

**Form fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | The message text |
| `source` | string | `"user"` | Message source (`"user"`, `"remind"`, etc.) |
| `session_id` | string | agent name | Override session id |

**Response:**
```json
{"content": "Agent's response text", "status": "ok"}
```

**Errors:**
- `404` — agent not found

---

### `GET /agents/{name}/events`
SSE stream of all events emitted by the agent's worker. The stream never terminates.

**Response:** `text/event-stream` of JSON lines:

```
{"event": "MessageQueued", "content": "hello", "source": "user", ...}
{"event": "MessageDequeued", "content": "hello", "source": "user", ...}
{"event": "RunContent", "content": "hello", "source": "user", "run_id": "...", ...}
{"event": "RunContent", "content": "Hello there!", "run_id": "...", ...}
{"event": "ToolCallStarted", "tools": [...], "run_id": "...", ...}
```

---

### `POST /agents/{agent_name}/sessions`
UI entry point. Creates a session, starts the agent's worker, pushes the message, and returns an SSE stream. The stream never terminates — it stays open for subsequent messages (e.g. program reminds).

**Form fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | The user's message |
| `stream` | string | `"true"` | Always SSE |
| `session_id` | string | agent's existing session or new UUID | Reuse existing session or create new |
| `source` | string | `"user"` | Message source |

**Response:** SSE stream starting with `RunStarted`, then forwarding from the agent's SSE channel:

```
{"event": "RunStarted", "session_id": "abc-123", "run_id": "...", "agent_name": "orchestrator"}
{"event": "RunContent", "content": "hello", "source": "user", ...}
{"event": "RunContent", "content": "Hello!", ...}
{"event": "ToolCallStarted", "tools": [...], ...}
```

**Event types:**
| Event | Description |
|-------|-------------|
| `RunStarted` | Stream opened, session created |
| `AgentRegistered` | A new agent was registered (emitted on orchestrator's stream) |
| `MessageQueued` | Message entered the queue |
| `MessageDequeued` | Worker picked up the message |
| `RunContent` | Text chunk. If `source` is set, it's a source marker (creates chat bubble). Without `source`, it's accumulated response text. |
| `ToolCallStarted` | Agent used a tool |
| `RunError` | Worker encountered an error processing a message |

---

### `POST /agents/{agent_name}/runs`
Ephemeral task execution. Creates a fresh agent instance (no persistent session), executes the task, and stores the result. Returns immediately with a run ID — execution happens in the background.

**Body (JSON):**
```json
{
  "task": "Summarize the README.md file",
  "schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
  "context": ["previous-run-id-1"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task` | string | yes | The task to execute |
| `schema` | object | no | JSON schema for structured output; triggers up to 3 retries on validation failure |
| `context` | array of strings | no | Run IDs whose summaries are prepended as context |

**Response:**
```json
{"run_id": "uuid", "status": "ok"}
```

**SSE events emitted during execution:**
| Event | Description |
|-------|-------------|
| `RunContent` | Accumulated text from the agent |
| `ToolCallStarted` | Agent used a tool |
| `RunError` | Execution error |
| `EphemeralRunCompleted` | Task finished, includes summary |

---

### `GET /runs/{run_id}`
Retrieve a stored ephemeral run result.

**Response:**
```json
{
  "id": "uuid",
  "agent": "orchestrator",
  "task": "Summarize the README.md file",
  "schema": null,
  "data": null,
  "text": "The README describes...",
  "summary": "A summary of the project's README.",
  "messages": [],
  "created_at": 1774641234,
  "completed_at": 1774641290
}
```

**Errors:**
- `404` — run not found

---

## Context Store

### `POST /context`
Save a context entry.

**Body (JSON):**
```json
{
  "text": "full agent output text",
  "summary": "one-line summary",
  "agent": "researcher",
  "tags": "insight,research",
  "run_id": "optional-run-uuid"
}
```

**Response:**
```json
{"id": "uuid", "summary": "one-line summary", "created_at": 1774641234}
```

---

### `GET /context`
Search context entries.

**Query params:** `q` (text search), `tags`, `agent`, `pinned` (bool), `limit` (default 50).

**Response:** `{"entries": [<ContextResult>, ...]}`

---

### `POST /context/{id}/pin`
Pin an entry so it always appears in recall results regardless of query.

**Response:** `{"status": "ok"}`

---

### `DELETE /context/{id}/pin`
Unpin an entry.

**Response:** `{"status": "ok"}`

---

### `DELETE /context/{id}`
Delete a context entry and its `.md` file.

**Response:** `{"status": "deleted"}`

---

## SDK — Context

### `ContextResult` object

`ContextResult` is a dict subclass returned by `run()`. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | context entry ID (uuid) |
| `summary` | str | one-line summary (`__str__` returns this) |
| `text` | str | full raw agent output |
| `data` | dict | structured fields (schema runs only), also accessible as dict keys |
| `agent` | str | agent name that produced it |
| `task` | str | original instruction |
| `file` | str | path to `~/.orchestrate/context/{id}.md` |

- `print(result)` prints the summary
- `result["key"]` accesses schema fields
- `result.text` accesses full output
- `result.id` for passing as context to subsequent runs

---

### `run()` — `context` parameter

The existing `run()` method gains a `context` parameter:

```python
context: list[ContextResult | str] | None = None
```

When passed, summaries are prepended to the prompt:

```
[Context from researcher (full output: ~/.orchestrate/context/abc-123.md)]:
Researcher found 3 key insights about X

<actual instruction here>
```

Each item can be a `ContextResult` object or a raw context entry ID string.

---

### Auto-save behavior

Every successful `run()` call automatically:
1. Calls `POST /context` with summary, text, agent, tags, run_id
2. Writes full output to `~/.orchestrate/context/{id}.md`
3. Returns a `ContextResult` wrapping all fields

---

### `recall(q="", tags="", agent="", limit=50) → list[ContextResult]`

Search saved context entries. Calls `GET /context` and returns hydrated `ContextResult` objects.

```python
past = await orch.recall(q="research", tags="insight", agent="researcher", limit=5)
```

---

### `pin(entry: ContextResult | str) → None`

Pin an entry. Calls `POST /context/{id}/pin`. Pinned entries always appear in recall results.

```python
await orch.pin(c1)
```

---

### `unpin(entry: ContextResult | str) → None`

Unpin an entry. Calls `DELETE /context/{id}/pin`.

```python
await orch.unpin(c1)
```

---

## Context file layout

```
~/.orchestrate/context/
  abc-123.md    # full output from one run
  def-456.md    # full output from another run
```

Each file contains:

````markdown
# Context: {agent} — {task[:60]}

**Agent**: {agent}
**Created**: {timestamp}
**Schema**: {schema or "none"}

## Summary
{summary}

## Full Output
{text}

## Structured Data
```json
{data}
```
````

---

## Example: full context flow

```python
import asyncio
from core import Orchestrate

async def main():
    orch = Orchestrate()

    # Step 1: run research agent — result auto-saved to context store
    c1 = await orch.run(
        "Research the top 3 Python async frameworks",
        to="researcher",
        schema={"findings": "str", "frameworks": "list"}
    )
    print(c1)            # prints summary
    print(c1["findings"]) # access schema field

    # Step 2: pin important result
    await orch.pin(c1)

    # Step 3: later, recall past research
    past = await orch.recall(q="async frameworks", limit=5)

    # Step 4: pass recalled context to another agent
    c2 = await orch.run(
        "Write a comparison blog post",
        to="writer",
        context=past      # summaries prepended to prompt
    )
    print(c2.file)        # path to full output markdown

asyncio.run(main())
```

---

## Session endpoints

Sessions are managed internally by the server (one per agent). These endpoints let the UI list and reload history.

### `GET /sessions`
List all sessions.

**Query params:** `session_type` (default `"agent"`), `component_id` (filters by `agent_id`)

**Response:**
```json
{
  "data": [
    {"session_id": "abc-123", "session_name": "hello 13:04", "agent_id": "orchestrator", "created_at": 1774641234, "updated_at": 1774641300}
  ]
}
```

### `GET /sessions/{session_id}/runs`
Message history for a session.

**Query params:** `session_type` (default `"agent"`)

**Response:**
```json
[{"run_input": "hello", "content": "Hi!", "tools": [], "created_at": 1774641234, "source": "user"}]
```

### `DELETE /sessions/{session_id}`
Delete a session and its run history.

**Response:** `{"status": "deleted"}`

---

## Backwards compatibility

### `POST /sessions/{session_id}/message`
Routes to the agent that owns the session. Same request/response shape as `POST /agents/{name}/message`.

**Errors:**
- `400` — no agent found for session

---

## Program integration

Programs running under orchestrate receive one env var:

| Variable | Value |
|----------|-------|
| `ORCHESTRATE_API_URL` | `http://localhost:7777` |

Typical pattern: call `POST /agents/{name}/message` with `source=remind` and block on the response. The agent name and session context are managed server-side.

## In-memory only

All state is in-memory. Server restart clears everything. This is by design for a dev tool.
