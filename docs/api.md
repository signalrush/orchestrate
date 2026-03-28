# Orchestrate REST API

Base URL: `http://localhost:7777`

## Architecture

```
Browser ←── SSE stream ←── AGENT_SSE queue ←── Worker emits events
                                                    ↑
POST /agents/{name}/runs ──→ AGENT_QUEUES ──→ Worker pulls & processes
POST /agents/{name}/message ──→ AGENT_QUEUES    (sequential, FIFO)
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
Register a new agent.

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
SSE stream of all events emitted by the agent's worker.

**Response:** `text/event-stream` of JSON lines:

```
{"event": "MessageQueued", "content": "hello", "source": "user", ...}
{"event": "MessageDequeued", "content": "hello", "source": "user", ...}
{"event": "RunContent", "content": "hello", "source": "user", "run_id": "...", ...}
{"event": "RunContent", "content": "Hello there!", "run_id": "...", ...}
{"event": "ToolCallStarted", "tools": [...], "run_id": "...", ...}
```

---

### `POST /agents/{id}/runs`
UI entry point. Creates a session, starts the agent's worker, pushes the message, and returns an SSE stream.

**Form fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | The user's message |
| `stream` | string | `"true"` | Always SSE |
| `session_id` | string | auto-generated | Reuse existing session or create new |
| `source` | string | `"user"` | Message source |

**Response:** SSE stream starting with `RunStarted`, then forwarding from the agent's SSE channel:

```
{"event": "RunStarted", "session_id": "abc-123", "run_id": "...", "agent_id": "orchestrator"}
{"event": "RunContent", "content": "hello", "source": "user", ...}
{"event": "RunContent", "content": "Hello!", ...}
{"event": "ToolCallStarted", "tools": [...], ...}
```

**Event types:**
| Event | Description |
|-------|-------------|
| `RunStarted` | Stream opened, session created |
| `MessageQueued` | Message entered the queue |
| `MessageDequeued` | Worker picked up the message |
| `RunContent` | Text chunk. If `source` is set, it's a source marker (creates chat bubble). Without `source`, it's accumulated response text. |
| `ToolCallStarted` | Agent used a tool |

---

## Session endpoints

Sessions are managed internally by the server (one per agent). These endpoints let the UI list and reload history.

### `GET /sessions`
List all sessions.

**Query params:** `type`, `component_id` (filters by `agent_id`), `db_id`

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

### `POST /sessions/{session_id}/program-done`
Signal that an orchestrate program has finished. Pushes a `{"type": "done"}` sentinel to the owning agent's queue.

**Response:** `{"status": "ok"}`

---

## Program integration

Programs running under orchestrate receive these env vars:

| Variable | Value |
|----------|-------|
| `ORCHESTRATE_API_URL` | `http://localhost:7777` |
| `ORCHESTRATE_SESSION_ID` | session id |
| `ORCHESTRATE_AGENT_NAME` | agent name |

Typical pattern: call `POST /agents/{name}/message` (or the compat `/sessions/{id}/message`) with `source=remind`, block on the response, then `POST /sessions/{id}/program-done` on exit.

## In-memory only

All state is in-memory. Server restart clears everything. This is by design for a dev tool.
