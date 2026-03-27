# Queue-Based Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Single queue per session processes user messages and remind calls in FIFO order through one open stream. User input is never blocked.

**Architecture:** The first `POST /agents/{id}/runs` creates a stream and queue. Subsequent messages (user or remind) go through `POST /sessions/{id}/message` which pushes to the queue and waits for the response via a future. The stream generator pulls items from the queue, runs SDK queries, and yields events. User input is always enabled.

**Tech Stack:** Python (FastAPI, asyncio), TypeScript (React/Next.js)

---

## File Structure

```
api/
  server.py                    # (REWRITE) queue-based streaming
src/orchestrate/
  core.py                      # (MODIFY) _remind_via_api posts to /message endpoint
  cli.py                       # (MODIFY) remove program-start, keep program-done
ui/src/
  hooks/useAIStreamHandler.tsx  # (MODIFY) handle source field in RunContent
  components/chat/ChatArea/
    ChatInput/ChatInput.tsx     # (MODIFY) never disable input, route to /message during stream
```

---

### Task 1: Rewrite API server with queue-based streaming

**Files:**
- Rewrite: `api/server.py`

- [ ] **Step 1: Rewrite `api/server.py`**

Replace the entire file with:

```python
"""REST API server bridging agent-ui to orchestrate.

Run: uvicorn api.server:app --port 7777
"""

import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Form, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from orchestrate.core import Auto, _parse_json

# Load OAuth token if available
try:
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(creds_path) and not os.environ.get("ANTHROPIC_API_KEY"):
        creds = json.load(open(creds_path))
        os.environ["ANTHROPIC_API_KEY"] = creds["claudeAiOauth"]["accessToken"]
except Exception:
    pass

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

app = FastAPI(title="orchestrate API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

AGENTS: dict[str, dict] = {
    "orchestrator": {
        "id": "orchestrator",
        "name": "Orchestrate Agent",
        "db_id": "default",
        "model": {"name": "claude-sonnet-4-6", "model": "claude-sonnet-4-6", "provider": "anthropic"},
    }
}

SESSIONS: dict[str, dict] = {}
RUNS: dict[str, list] = {}
AUTOS: dict[str, Auto] = {}

# Per-session: queue of items to process, each item is a dict with a future
SESSION_QUEUES: dict[str, asyncio.Queue] = {}

# How long the queue processor waits for next item before closing stream
QUEUE_IDLE_TIMEOUT = 300  # 5 minutes


def _get_or_create_auto(session_id: str, agent_id: str) -> Auto:
    if session_id not in AUTOS:
        agent_config = AGENTS.get(agent_id, AGENTS["orchestrator"])
        model = agent_config.get("model", {}).get("model", "claude-sonnet-4-6")
        AUTOS[session_id] = Auto(model=model)
    return AUTOS[session_id]


def _ensure_session(session_id: str, agent_id: str) -> dict:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {
            "session_id": session_id,
            "session_name": f"Session {len(SESSIONS) + 1}",
            "agent_id": agent_id,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        RUNS[session_id] = []
    return SESSIONS[session_id]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/agents")
async def list_agents():
    return list(AGENTS.values())


@app.get("/teams")
async def list_teams():
    return []


@app.get("/sessions")
async def list_sessions(
    type: str = Query("agent"),
    component_id: str = Query(""),
    db_id: str = Query(""),
):
    sessions = [
        s for s in SESSIONS.values()
        if not component_id or s.get("agent_id") == component_id
    ]
    sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
    return {"data": sessions}


@app.get("/sessions/{session_id}/runs")
async def get_session_runs(
    session_id: str,
    type: str = Query("agent"),
    db_id: str = Query(""),
):
    return RUNS.get(session_id, [])


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db_id: str = Query("")):
    SESSIONS.pop(session_id, None)
    RUNS.pop(session_id, None)
    AUTOS.pop(session_id, None)
    SESSION_QUEUES.pop(session_id, None)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Queue-based message endpoint
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/message")
async def post_message(
    session_id: str,
    message: str = Form(...),
    source: str = Form("user"),
):
    """Push a message to the session queue. Blocks until processed, returns response."""
    if session_id not in SESSION_QUEUES:
        return JSONResponse({"error": "no active stream for session"}, status_code=400)

    loop = asyncio.get_event_loop()
    future = loop.create_future()

    await SESSION_QUEUES[session_id].put({
        "message": message,
        "source": source,
        "future": future,
    })

    # Wait for the queue processor to finish handling this item
    result = await future
    return JSONResponse({"content": result, "status": "ok"})


@app.post("/sessions/{session_id}/program-done")
async def program_done(session_id: str):
    """Signal that the orchestrate program has finished."""
    if session_id in SESSION_QUEUES:
        await SESSION_QUEUES[session_id].put({"type": "done"})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Stream endpoint with queue processor
# ---------------------------------------------------------------------------

@app.post("/agents/{agent_id}/runs")
async def run_agent(
    agent_id: str,
    message: str = Form(...),
    stream: str = Form("true"),
    session_id: str = Form(""),
    source: str = Form("user"),
):
    if not session_id:
        session_id = str(uuid.uuid4())
    _ensure_session(session_id, agent_id)

    auto = _get_or_create_auto(session_id, agent_id)
    run_id = str(uuid.uuid4())
    now = int(time.time())

    async def generate():
        # Create queue for this session
        queue = asyncio.Queue()
        SESSION_QUEUES[session_id] = queue

        # RunStarted
        yield json.dumps({
            "event": "RunStarted",
            "session_id": session_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "content_type": "text/plain",
            "created_at": now,
        })

        try:
            # Process the initial message
            async for event_str in _process_message(message, source, agent_id, session_id, auto, run_id):
                yield event_str

            # Loop: pull from queue and process
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=QUEUE_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    break

                if item.get("type") == "done":
                    break

                item_source = item["source"]
                item_message = item["message"]
                item_future = item.get("future")
                item_run_id = str(uuid.uuid4())

                # Yield source marker so UI knows what bubble to create
                yield json.dumps({
                    "event": "RunContent",
                    "content": item_message,
                    "content_type": "text/plain",
                    "source": item_source,
                    "session_id": session_id,
                    "run_id": item_run_id,
                    "created_at": int(time.time()),
                })

                # Process and stream the response
                response_text = ""
                async for event_str in _process_message(item_message, item_source, agent_id, session_id, auto, item_run_id):
                    yield event_str
                    # Extract accumulated text for the future
                    try:
                        ev = json.loads(event_str)
                        if ev.get("event") == "RunContent" and "source" not in ev:
                            response_text = ev.get("content", "")
                    except json.JSONDecodeError:
                        pass

                # Resolve the future so the POST caller gets the response
                if item_future and not item_future.done():
                    item_future.set_result(response_text)

            # RunCompleted
            yield json.dumps({
                "event": "RunCompleted",
                "content": "",
                "content_type": "text/plain",
                "session_id": session_id,
                "run_id": run_id,
                "created_at": int(time.time()),
            })

        except Exception as e:
            yield json.dumps({
                "event": "RunError",
                "content": str(e),
                "content_type": "text/plain",
                "session_id": session_id,
                "run_id": run_id,
                "created_at": int(time.time()),
            })

        finally:
            SESSION_QUEUES.pop(session_id, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _process_message(message, source, agent_id, session_id, auto, run_id):
    """Run an Agent SDK query and yield streaming events."""
    accumulated_text = ""
    tools_used = []

    async for msg in query(
        prompt=message,
        options=ClaudeAgentOptions(
            allowed_tools=[
                "Read", "Edit", "Write", "Bash", "Glob", "Grep",
                "Agent", "WebSearch", "WebFetch", "Skill",
            ],
            permission_mode="bypassPermissions",
            model=AGENTS.get(agent_id, {}).get("model", {}).get("model", "claude-sonnet-4-6"),
            resume=auto._sessions.get("self", {}).get("session_id"),
            setting_sources=["user"],
            env={
                "ORCHESTRATE_API_URL": "http://localhost:7777",
                "ORCHESTRATE_SESSION_ID": session_id,
            },
        ),
    ):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if hasattr(block, "text"):
                    accumulated_text += block.text
                    yield json.dumps({
                        "event": "RunContent",
                        "content": accumulated_text,
                        "content_type": "text/plain",
                        "session_id": session_id,
                        "run_id": run_id,
                        "created_at": int(time.time()),
                    })
                elif hasattr(block, "name"):
                    tool_record = {
                        "role": "tool",
                        "content": None,
                        "tool_call_id": getattr(block, "id", str(uuid.uuid4())),
                        "tool_name": block.name,
                        "tool_args": getattr(block, "input", {}),
                        "tool_call_error": False,
                        "metrics": {"time": 0},
                        "created_at": int(time.time()),
                    }
                    tools_used.append(tool_record)
                    yield json.dumps({
                        "event": "ToolCallStarted",
                        "tools": [tool_record],
                        "content_type": "text/plain",
                        "session_id": session_id,
                        "run_id": run_id,
                        "created_at": int(time.time()),
                    })
        elif isinstance(msg, ResultMessage):
            if "self" not in auto._sessions:
                auto.agent("self")
            auto._sessions["self"]["session_id"] = msg.session_id

    # Store run
    RUNS.setdefault(session_id, []).append({
        "run_input": message,
        "content": accumulated_text,
        "tools": tools_used,
        "created_at": int(time.time()),
        "source": source,
    })
    SESSIONS[session_id]["updated_at"] = int(time.time())


# ---------------------------------------------------------------------------
# Dynamic agent registration
# ---------------------------------------------------------------------------

@app.post("/agents")
async def register_agent(request: Request):
    data = await request.json()
    agent_id = data.get("id", str(uuid.uuid4()))
    AGENTS[agent_id] = {
        "id": agent_id,
        "name": data.get("name", agent_id),
        "db_id": data.get("db_id", "default"),
        "model": data.get("model", {"name": "claude-sonnet-4-6", "model": "claude-sonnet-4-6", "provider": "anthropic"}),
    }
    return AGENTS[agent_id]
```

- [ ] **Step 2: Run existing tests**

```bash
pytest tests/test_api.py -v
```

Expected: All pass (the API surface is the same, just internal restructuring).

- [ ] **Step 3: Commit**

```bash
git add -f api/server.py
git commit -m "refactor: queue-based streaming with FIFO message processing"
```

---

### Task 2: Update core library — remind posts to /message endpoint

**Files:**
- Modify: `src/orchestrate/core.py:143-181`

- [ ] **Step 1: Update `_remind_via_api` to POST to `/sessions/{id}/message`**

Replace the `_remind_via_api` method:

```python
    async def _remind_via_api(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Send remind via HTTP POST to the session message endpoint."""
        import urllib.request
        import urllib.parse

        prompt = instruction
        if schema:
            schema_desc = json.dumps(schema, indent=2)
            prompt += f"\n\nRespond with a JSON object with these keys and types:\n{schema_desc}"

        data = urllib.parse.urlencode({
            "message": prompt,
            "source": "remind",
        }).encode()

        req = urllib.request.Request(
            f"{self._api_url}/sessions/{self._session_id}/message",
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

        print(f"[remind] {result_text[:200]}", flush=True)

        if schema:
            return _parse_json(result_text, schema)
        return result_text
```

- [ ] **Step 2: Commit**

```bash
git add -f src/orchestrate/core.py
git commit -m "feat: remind posts to /sessions/{id}/message endpoint"
```

---

### Task 3: Update CLI — remove program-start, keep program-done

**Files:**
- Modify: `src/orchestrate/cli.py:90-108`

- [ ] **Step 1: Remove program-start call from `_exec_program`**

Remove the program-start block (lines 95-108). Keep the program-done block in the `finally` clause. The function should start like:

```python
def _exec_program(file_path: str, run_id: str, run_dir_path: str) -> None:
    """Import and run user's async main(auto) in-process. Updates run.json on finish."""
    run_dir = Path(run_dir_path)
    data = json.loads((run_dir / "run.json").read_text())

    try:
        spec = importlib.util.spec_from_file_location("_user_program", file_path)
```

The `finally` block still calls `/sessions/{id}/program-done`.

- [ ] **Step 2: Reinstall and run tests**

```bash
pip install -e .
pytest tests/ -v
```

- [ ] **Step 3: Commit**

```bash
git add -f src/orchestrate/cli.py
git commit -m "fix: remove program-start, keep program-done signal"
```

---

### Task 4: UI — handle source field in stream handler

**Files:**
- Modify: `ui/src/hooks/useAIStreamHandler.tsx`

- [ ] **Step 1: Add source handling to RunContent event**

In `useAIStreamHandler.tsx`, find the `RunContent` handler (around line 221). Add a source check before the existing content handling. The full block should be:

```typescript
            } else if (
              chunk.event === RunEvent.RunContent ||
              chunk.event === RunEvent.TeamRunContent
            ) {
              // Handle source-tagged events: create new bubble
              if ((chunk as any).source === 'remind' || (chunk as any).source === 'user') {
                const role = (chunk as any).source === 'remind' ? 'remind' : 'user'
                setMessages((prevMessages) => {
                  const newMessages = [...prevMessages]
                  // Add source bubble (remind or user)
                  newMessages.push({
                    role: role as any,
                    content: typeof chunk.content === 'string' ? chunk.content : '',
                    created_at: chunk.created_at ?? Math.floor(Date.now() / 1000)
                  })
                  // Add empty agent bubble for the response
                  newMessages.push({
                    role: 'agent',
                    content: '',
                    tool_calls: [],
                    streamingError: false,
                    created_at: (chunk.created_at ?? Math.floor(Date.now() / 1000)) + 1
                  })
                  lastContent = ''
                  return newMessages
                })
                return
              }
              setMessages((prevMessages) => {
```

This goes right after the `} else if (chunk.event === RunEvent.RunContent` line and before the existing `setMessages` call.

- [ ] **Step 2: Commit**

```bash
git add -f ui/src/hooks/useAIStreamHandler.tsx
git commit -m "feat: stream handler creates remind/user bubbles from source field"
```

---

### Task 5: UI — never disable input, route to /message during stream

**Files:**
- Modify: `ui/src/components/chat/ChatArea/ChatInput/ChatInput.tsx`

- [ ] **Step 1: Remove isStreaming from button disabled and keydown check**

In `ChatInput.tsx`, make these changes:

1. Remove `!isStreaming` from the Enter key handler (line 47):

```typescript
          if (
            e.key === 'Enter' &&
            !e.nativeEvent.isComposing &&
            !e.shiftKey
          ) {
```

2. Remove `isStreaming` from button disabled (line 60):

```typescript
        disabled={
          !(selectedAgent || teamId) || !inputMessage.trim()
        }
```

3. Update `handleSubmit` to route to `/message` when streaming is active. Replace the entire `handleSubmit`:

```typescript
  const handleSubmit = async () => {
    if (!inputMessage.trim()) return

    const currentMessage = inputMessage
    setInputMessage('')

    try {
      if (isStreaming && sessionId) {
        // During active stream: push to queue via /message endpoint
        const endpointUrl = constructEndpointUrl(selectedEndpoint)
        const formData = new FormData()
        formData.append('message', currentMessage)
        formData.append('source', 'user')
        await fetch(`${endpointUrl}/sessions/${sessionId}/message`, {
          method: 'POST',
          body: formData,
        })
      } else {
        // No active stream: create new stream
        await handleStreamResponse(currentMessage)
      }
    } catch (error) {
      toast.error(
        `Error in handleSubmit: ${
          error instanceof Error ? error.message : String(error)
        }`
      )
    }
  }
```

4. Add missing imports at the top:

```typescript
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'
```

5. Add missing query state:

```typescript
  const [sessionId] = useQueryState('session')
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
```

- [ ] **Step 2: Commit**

```bash
git add -f ui/src/components/chat/ChatArea/ChatInput/ChatInput.tsx
git commit -m "feat: input always enabled, routes to /message during active stream"
```

---

### Task 6: End-to-end test

- [ ] **Step 1: Restart servers**

```bash
pkill -f "uvicorn api.server" 2>/dev/null
pip install -e .
PYTHONPATH=/path/to/orchestrate uvicorn api.server:app --port 7777 --host 0.0.0.0 &
# UI should auto-reload from dev server
sleep 3
curl -s http://localhost:7777/health
```

- [ ] **Step 2: Test simple message (no queue wait)**

```bash
SESSION_ID="test-$(date +%s)"
timeout 10 curl -s -X POST http://localhost:7777/agents/orchestrator/runs \
  -F "message=say hi" -F "stream=true" -F "session_id=$SESSION_ID"
```

Expected: RunStarted → RunContent → RunCompleted in < 10 seconds (no idle wait).

- [ ] **Step 3: Test queue-based message flow**

```bash
SESSION_ID="queue-test-$(date +%s)"
# Start a stream (agent responds, then waits on queue)
curl -s -X POST http://localhost:7777/agents/orchestrator/runs \
  -F "message=hello" -F "stream=true" -F "session_id=$SESSION_ID" &
STREAM_PID=$!
sleep 10

# Push a remind message to the queue
curl -s -X POST "http://localhost:7777/sessions/$SESSION_ID/message" \
  -F "message=say hello" -F "source=remind"

# Push a user message to the queue
curl -s -X POST "http://localhost:7777/sessions/$SESSION_ID/message" \
  -F "message=what time is it" -F "source=user"

# Signal done
curl -s -X POST "http://localhost:7777/sessions/$SESSION_ID/program-done"

wait $STREAM_PID
```

Expected: Stream includes initial response, then remind bubble + response, then user bubble + response, then RunCompleted.

- [ ] **Step 4: Verify in UI**

Open http://localhost:3000, start a new chat. Ask the agent to run an orchestrate program. Verify:
- Remind bubbles appear with "R" icon
- User can type while program is running
- Messages process in order

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "test: verify queue-based streaming end-to-end"
```
