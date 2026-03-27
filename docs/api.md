# Orchestrate REST API

Base URL: `http://localhost:7777`

## Architecture

```
Browser ←── SSE stream ←── SESSION_SSE queue ←── Worker emits events
                                                      ↑
POST /agents/{id}/runs ──→ SESSION_QUEUES ──→ Worker pulls & processes
POST /sessions/{id}/message ──→ SESSION_QUEUES     (sequential, FIFO)
Program POST /sessions/{id}/message ──→ SESSION_QUEUES
```

Each session has:
- **Queue** (`SESSION_QUEUES`): input — all messages go here
- **Worker** (`SESSION_WORKERS`): background task — processes queue items one by one
- **SSE channel** (`SESSION_SSE`): output — worker pushes events, stream reads them

## Endpoints

### `GET /health`
Health check.

**Response:** `{"status": "ok"}`

---

### `GET /agents`
List registered agents.

**Response:** `[{"id": "orchestrator", "name": "Orchestrate Agent", "model": {...}}]`

---

### `POST /agents`
Register a new agent.

**Body (JSON):**
```json
{"id": "my-agent", "name": "My Agent", "model": {"model": "claude-opus-4-6"}}
```

---

### `POST /agents/{agent_id}/runs`
Start a new conversation turn. Creates session, starts worker, pushes message to queue, returns SSE stream.

**Form fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | The user's message |
| `stream` | string | `"true"` | Always true (SSE) |
| `session_id` | string | auto-generated | Reuse existing session or create new |
| `source` | string | `"user"` | Message source (`user`) |

**Response:** SSE stream of JSON events:

```
{"event": "RunStarted", "session_id": "abc-123", "run_id": "...", "agent_id": "orchestrator"}
{"event": "RunContent", "content": "hi", "source": "user", "session_id": "abc-123"}
{"event": "RunContent", "content": "Hello!", "session_id": "abc-123"}
{"event": "ToolCallStarted", "tools": [...], "session_id": "abc-123"}
{"event": "RunContent", "content": "say hello", "source": "remind", "session_id": "abc-123"}
{"event": "RunContent", "content": "Hello there!", "session_id": "abc-123"}
{"event": "RunCompleted", "content": "", "session_id": "abc-123"}
```

**Event types:**
| Event | Description |
|-------|-------------|
| `RunStarted` | Stream opened, session created |
| `RunContent` | Text content. If `source` is set, it's a source marker (creates bubble). If no `source`, it's accumulated response text. |
| `ToolCallStarted` | Agent used a tool |
| `RunCompleted` | Worker finished, stream ends |
| `RunError` | Error occurred |

**Source markers:** When the worker starts processing a queue item, it emits a `RunContent` with a `source` field:
- `source: "user"` → frontend creates user bubble + empty agent bubble
- `source: "remind"` → frontend creates remind bubble + empty agent bubble

Subsequent `RunContent` events (without `source`) fill the agent bubble with accumulated text.

---

### `POST /sessions/{session_id}/message`
Push a message to an active session's queue. **Blocks until the worker processes it and returns the response.** Used by:
- Frontend (follow-up messages during active stream)
- Programs (`auto.remind()` calls)

**Form fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | string | required | The message text |
| `source` | string | `"user"` | `"user"` or `"remind"` |

**Response:**
```json
{"content": "Agent's response text", "status": "ok"}
```

**Errors:**
- `400` — no active worker/queue for this session

---

### `POST /sessions/{session_id}/program-done`
Signal that an orchestrate program has finished. Pushes a `{"type": "done"}` sentinel to the queue, causing the worker to stop after processing remaining items.

**Response:** `{"status": "ok"}`

---

### `GET /sessions`
List all sessions.

**Query params:** `type`, `component_id`, `db_id` (all optional, for filtering)

**Response:**
```json
{
  "data": [
    {
      "session_id": "abc-123",
      "session_name": "hello 13:04",
      "agent_id": "orchestrator",
      "created_at": 1774641234,
      "updated_at": 1774641300
    }
  ]
}
```

---

### `GET /sessions/{session_id}/runs`
Get stored runs (message history) for a session. Used by frontend to reload session on navigation.

**Response:**
```json
[
  {
    "run_input": "hello",
    "content": "Hi! How can I help?",
    "tools": [],
    "created_at": 1774641234,
    "source": "user"
  },
  {
    "run_input": "say something random",
    "content": "Octopuses have three hearts!",
    "tools": [],
    "created_at": 1774641250,
    "source": "remind"
  }
]
```

---

### `DELETE /sessions/{session_id}`
Delete a session and all associated state (queue, worker, SSE channel, runs).

**Response:** `{"status": "deleted"}`

---

## Worker Lifecycle

1. **Created** by `_ensure_worker()` on first `POST /agents/{id}/runs`
2. **Processes** queue items sequentially (FIFO)
3. **Emits** source marker → runs `_process_message()` → emits RunContent/ToolCallStarted events
4. **Resolves** futures for `/message` callers
5. **Stops** on `program-done` signal or `QUEUE_IDLE_TIMEOUT` (5 min)
6. **Emits** `RunCompleted` and cleans up

## Program Integration

Programs run via `orchestrate-run <file.py>` as background processes. They:
1. Get `ORCHESTRATE_API_URL` and `ORCHESTRATE_SESSION_ID` from environment
2. Call `auto.remind(instruction)` → posts to `POST /sessions/{id}/message` with `source=remind`
3. Block until the worker processes the remind and returns the response
4. On exit, send `POST /sessions/{id}/program-done`

## In-Memory Only

All state is in-memory. Server restart clears everything. This is by design for a dev tool.
