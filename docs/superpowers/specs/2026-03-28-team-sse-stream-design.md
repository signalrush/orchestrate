# Team SSE Stream Design

**Goal:** Replace per-agent SSE queues with a single team stream. All agent events flow through one SSE connection, tagged with `agent_name`. No more shared queue / event stealing.

## Architecture

```
Browser ←── GET /teams/default/events ←── TEAM_SSE queue
                                              ↑
                              Worker pushes events tagged with agent_name
                                              ↑
POST /agents/{name}/message ──→ AGENT_QUEUES ──→ Worker processes
```

One SSE queue per team. One reader (the browser). Workers for all agents push to the same team queue.

## Flow

### Page load
1. Frontend opens `GET /teams/default/events` → persistent SSE stream
2. Loads sessions from `GET /sessions` → sidebar populates
3. If URL has session_id → loads runs from `GET /sessions/{id}/runs`

### Sending a message
1. Frontend fires `POST /agents/{name}/message` (fire-and-forget, don't await)
2. Server queues message, worker processes, events pushed to team SSE
3. Frontend receives events via SSE, renders bubbles
4. POST eventually returns response (UI ignores it — programs use it)

### Switching sessions
1. User clicks session in sidebar
2. Frontend loads history: `GET /sessions/{id}/runs` → renders messages
3. Frontend changes filter: only render events where `agent_name` matches the viewed agent
4. SSE stream stays open — same connection, no reconnect

### Program spawns agents
1. `orch.agent("joker")` → `POST /agents` → server emits `AgentRegistered` on team SSE
2. Sidebar refreshes
3. `orch.run("tell joke", to="joker")` → worker processes → events on team SSE with `agent_name="joker"`

### Browser refresh
1. Reconnect `GET /teams/default/events`
2. Load sessions + current session runs from API

## Server changes

### Replace per-agent SSE with team SSE

Remove:
- `AGENT_SSE: dict[str, asyncio.Queue]` — per-agent SSE queues

Add:
- `TEAM_SSE: asyncio.Queue` — single team queue

### `_emit_agent` → `_emit`

Push to `TEAM_SSE` instead of `AGENT_SSE[name]`. Always include `agent_name` in the event.

### New endpoint: `GET /teams/default/events`

```python
@app.get("/teams/default/events")
async def team_events():
    async def generate():
        while True:
            event_str = await TEAM_SSE.get()
            yield event_str
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### Update `POST /agents/{name}/message`

No changes — it already pushes to agent queue, worker processes, emits events. Events now go to team SSE instead of agent SSE.

### Remove `POST /agents/{name}/runs`

The UI no longer needs this. Messages go through `/agents/{name}/message`. SSE is persistent via `/teams/default/events`.

Keep it for backwards compat but it just pushes the message and returns the team SSE stream.

### `_ensure_agent_worker`

No longer creates `AGENT_SSE[name]`. Workers push to `TEAM_SSE`.

## Frontend changes

### On page load: open team SSE

In a top-level component or hook, open `GET /teams/default/events` via `EventSource` or `fetch` stream. Process events same as current `useAIStreamHandler` but filter by the currently viewed agent.

### `handleStreamResponse` → `sendMessage`

No longer opens a stream. Just fires `POST /agents/{name}/message` (fire-and-forget). The team SSE delivers events.

### Session switching

Load history from API. Change the `agent_name` filter. No reconnect.

## What changes

| Component | Before | After |
|-----------|--------|-------|
| SSE queues | One per agent (`AGENT_SSE`) | One per team (`TEAM_SSE`) |
| SSE endpoint | `POST /agents/{name}/runs` (per message) | `GET /teams/default/events` (persistent) |
| Send message | Opens stream + sends | Fire-and-forget POST |
| Session switch | Load from API (no SSE) | Load from API + change filter |
| Event routing | Server routes to agent queue | Frontend filters by `agent_name` |

## What doesn't change

- Agent queues, workers — same
- `/agents/{name}/message` — same
- `/agents` registration — same
- SQLite persistence — same
- Session/runs storage — same
