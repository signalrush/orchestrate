# API-First Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Orchestrate` a pure HTTP client. Move all Claude Agent SDK calls behind the REST API. Every `orch.run()` goes through the API.

**Architecture:** Three layers — Orchestrate (HTTP client) → REST API (FastAPI) → Claude Agent SDK. The server manages all agents keyed by name. Each agent gets a config, session, queue, worker, and SSE channel. The client only uses `urllib` — no SDK dependency.

**Tech Stack:** Python (FastAPI, asyncio, urllib), Claude Agent SDK (server-side only)

---

## File Structure

```
api/
  server.py                    # (REWRITE) agent-keyed architecture, POST /agents/{name}/message
src/orchestrate/
  core.py                      # (REWRITE) pure HTTP client, ~60 lines
  cli.py                       # (MODIFY) remove session_id env var
tests/
  test_api.py                  # (MODIFY) update tests for new endpoints
```

---

### Task 1: Rewrite server.py — agent-keyed stores and endpoints

**Files:**
- Rewrite: `api/server.py`

The server currently keys everything by `session_id`. Refactor to key by `agent_name`. Each agent gets its own session, queue, worker, SSE channel.

- [ ] **Step 1: Replace session-keyed stores with agent-keyed stores**

Remove:
```python
ORCHESTRATORS: dict[str, Orchestrate] = {}
SESSION_QUEUES: dict[str, asyncio.Queue] = {}
SESSION_SSE: dict[str, asyncio.Queue] = {}
SESSION_WORKERS: dict[str, asyncio.Task] = {}
TEAMS: dict[str, dict] = {}
SESSION_TO_TEAM: dict[str, dict] = {}
```

Replace with:
```python
# Per-agent state (keyed by agent name)
AGENT_SESSIONS: dict[str, str] = {}          # agent_name → session_id (for resume)
AGENT_QUEUES: dict[str, asyncio.Queue] = {}  # agent_name → input queue
AGENT_SSE: dict[str, asyncio.Queue] = {}     # agent_name → SSE output channel
AGENT_WORKERS: dict[str, asyncio.Task] = {}  # agent_name → background worker task
```

- [ ] **Step 2: Rewrite `POST /agents` to store full config**

```python
@app.post("/agents")
async def register_agent(request: Request):
    data = await request.json()
    name = data["name"]
    AGENTS[name] = {
        "id": name,
        "name": name,
        "db_id": "default",
        "cwd": data.get("cwd"),
        "model": data.get("model", "claude-opus-4-6"),
        "tools": data.get("tools", ALL_TOOLS),
        "prompt": data.get("prompt"),
    }
    return {"name": name, "status": "registered"}
```

- [ ] **Step 3: Add `POST /agents/{name}/message`**

New endpoint — the core messaging primitive. Looks up agent by name, ensures queue/worker exist, pushes message, blocks on future.

```python
@app.post("/agents/{agent_name}/message")
async def agent_message(
    agent_name: str,
    message: str = Form(...),
    source: str = Form("run"),
):
    if agent_name not in AGENTS:
        return JSONResponse({"error": f"agent '{agent_name}' not registered"}, status_code=404)

    _ensure_agent_worker(agent_name)

    loop = asyncio.get_event_loop()
    future = loop.create_future()

    await AGENT_QUEUES[agent_name].put({
        "message": message,
        "source": source,
        "future": future,
    })

    _emit_agent(agent_name, {
        "event": "MessageQueued",
        "content": message,
        "source": source,
        "agent_name": agent_name,
        "created_at": int(time.time()),
    })

    result = await future
    return JSONResponse({"content": result, "status": "ok"})
```

- [ ] **Step 4: Add `GET /agents/{name}/events`**

SSE stream for an agent. Used by UI to watch agent activity.

```python
@app.get("/agents/{agent_name}/events")
async def agent_events(agent_name: str):
    if agent_name not in AGENTS:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    _ensure_agent_worker(agent_name)
    sse = AGENT_SSE.get(agent_name)
    if not sse:
        return JSONResponse({"error": "no SSE channel"}, status_code=500)

    async def generate():
        while True:
            event_str = await sse.get()
            yield event_str

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 5: Add `DELETE /agents/{name}`**

```python
@app.delete("/agents/{agent_name}")
async def remove_agent(agent_name: str):
    AGENTS.pop(agent_name, None)
    AGENT_SESSIONS.pop(agent_name, None)
    AGENT_QUEUES.pop(agent_name, None)
    AGENT_SSE.pop(agent_name, None)
    worker = AGENT_WORKERS.pop(agent_name, None)
    if worker and not worker.done():
        worker.cancel()
    # Also clean up SESSIONS/RUNS for this agent's session
    for sid, sess in list(SESSIONS.items()):
        if sess.get("agent_id") == agent_name:
            SESSIONS.pop(sid, None)
            RUNS.pop(sid, None)
    return {"status": "deleted"}
```

- [ ] **Step 6: Rewrite `_ensure_agent_worker` and `_agent_worker`**

```python
def _ensure_agent_worker(agent_name: str):
    if agent_name not in AGENT_WORKERS or AGENT_WORKERS[agent_name].done():
        if agent_name not in AGENT_QUEUES:
            AGENT_QUEUES[agent_name] = asyncio.Queue()
        if agent_name not in AGENT_SSE:
            AGENT_SSE[agent_name] = asyncio.Queue()
        if agent_name not in AGENT_SESSIONS:
            session_id = str(uuid.uuid4())
            _ensure_session(session_id, agent_name)
            AGENT_SESSIONS[agent_name] = session_id
        AGENT_WORKERS[agent_name] = asyncio.create_task(
            _agent_worker(agent_name)
        )


async def _agent_worker(agent_name: str):
    queue = AGENT_QUEUES[agent_name]
    config = AGENTS.get(agent_name, {})
    session_id = AGENT_SESSIONS[agent_name]
    resume_id = None  # tracks Claude session for resume

    while True:
        item = await queue.get()
        if item.get("type") == "done":
            continue

        item_source = item["source"]
        item_message = item["message"]
        item_future = item.get("future")
        item_run_id = str(uuid.uuid4())

        _emit_agent(agent_name, {
            "event": "MessageDequeued",
            "content": item_message,
            "source": item_source,
            "agent_name": agent_name,
            "created_at": int(time.time()),
        })

        _emit_agent(agent_name, {
            "event": "RunContent",
            "content": item_message,
            "content_type": "text/plain",
            "source": item_source,
            "agent_name": agent_name,
            "session_id": session_id,
            "run_id": item_run_id,
            "created_at": int(time.time()),
        })

        response_text, new_resume_id = await _process_agent_message(
            item_message, item_source, agent_name, session_id, config, resume_id, item_run_id
        )
        resume_id = new_resume_id

        if item_future and not item_future.done():
            item_future.set_result(response_text)
```

- [ ] **Step 7: Rewrite `_process_message` → `_process_agent_message`**

Reads config from `AGENTS[agent_name]` instead of from `Orchestrate` object.

```python
async def _process_agent_message(message, source, agent_name, session_id, config, resume_id, run_id):
    accumulated_text = ""
    tools_used = []

    model = config.get("model", "claude-opus-4-6")
    cwd = config.get("cwd")
    tools = config.get("tools", ALL_TOOLS)
    system_prompt = config.get("prompt")

    opts = ClaudeAgentOptions(
        allowed_tools=tools,
        permission_mode="bypassPermissions",
        model=model,
        effort="max",
        resume=resume_id,
        setting_sources=["user"],
        cwd=cwd,
        system_prompt=system_prompt,
        env={
            "ORCHESTRATE_API_URL": "http://localhost:7777",
        },
    )

    async for msg in query(prompt=message, options=opts):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if hasattr(block, "text"):
                    if accumulated_text and not accumulated_text[-1].isspace() and block.text and not block.text[0].isspace():
                        accumulated_text += " "
                    accumulated_text += block.text
                    _emit_agent(agent_name, {
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
                    _emit_agent(agent_name, {
                        "event": "ToolCallStarted",
                        "tools": [tool_record],
                        "content_type": "text/plain",
                        "session_id": session_id,
                        "run_id": run_id,
                        "created_at": int(time.time()),
                    })
        elif isinstance(msg, ResultMessage):
            resume_id = msg.session_id

    RUNS.setdefault(session_id, []).append({
        "run_input": message,
        "content": accumulated_text,
        "tools": tools_used,
        "created_at": int(time.time()),
        "source": source,
    })
    SESSIONS[session_id]["updated_at"] = int(time.time())

    return accumulated_text, resume_id
```

- [ ] **Step 8: Add `_emit_agent` helper**

```python
def _emit_agent(agent_name: str, event: dict):
    sse = AGENT_SSE.get(agent_name)
    if sse:
        sse.put_nowait(json.dumps(event))
```

- [ ] **Step 9: Update `POST /agents/{agent_id}/runs` to use agent-keyed stores**

The UI's entry point. Pre-registers the "orchestrator" agent if needed, pushes message to its queue, returns SSE from its channel.

```python
@app.post("/agents/{agent_id}/runs")
async def run_agent(
    agent_id: str,
    message: str = Form(...),
    stream: str = Form("true"),
    session_id: str = Form(""),
    source: str = Form("user"),
):
    if agent_id not in AGENTS:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    _ensure_agent_worker(agent_id)

    if not session_id:
        session_id = AGENT_SESSIONS.get(agent_id, str(uuid.uuid4()))

    if SESSIONS.get(session_id, {}).get("session_name", "").startswith("Session "):
        SESSIONS[session_id]["session_name"] = message[:40] + " " + time.strftime("%H:%M")

    run_id = str(uuid.uuid4())
    now = int(time.time())

    await AGENT_QUEUES[agent_id].put({
        "message": message,
        "source": source,
    })

    async def generate():
        yield json.dumps({
            "event": "RunStarted",
            "session_id": session_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "content_type": "text/plain",
            "created_at": now,
        })

        sse = AGENT_SSE.get(agent_id)
        if not sse:
            return

        while True:
            event_str = await sse.get()
            yield event_str

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 10: Remove team endpoints and session-to-team mapping**

Delete: `POST /teams`, `POST /teams/{id}/members`, `POST /teams/{id}/runs`, `GET /teams`, `TEAMS` dict, `SESSION_TO_TEAM` dict. The agent-keyed model replaces teams — each named agent IS its own independent entity.

- [ ] **Step 11: Keep backwards-compat `POST /sessions/{id}/message`**

Route to the agent that owns this session:

```python
@app.post("/sessions/{session_id}/message")
async def post_message(session_id: str, message: str = Form(...), source: str = Form("user")):
    # Find which agent owns this session
    for agent_name, sid in AGENT_SESSIONS.items():
        if sid == session_id:
            return await agent_message(agent_name, message, source)
    return JSONResponse({"error": "no agent for this session"}, status_code=400)
```

- [ ] **Step 12: Commit**

```bash
git add -f api/server.py
git commit -m "refactor: agent-keyed server architecture"
```

---

### Task 2: Rewrite core.py — pure HTTP client

**Files:**
- Rewrite: `src/orchestrate/core.py`

- [ ] **Step 1: Strip SDK imports, rewrite Orchestrate class**

```python
"""orchestrate core — Orchestrate class (pure HTTP client)."""

import json
import re
import urllib.request
import urllib.parse


def _parse_json(text: str, schema: dict) -> dict:
    # (keep existing _parse_json unchanged)
    ...


class Orchestrate:
    """Pure HTTP client for the orchestrate API.

    orch.run(instruction) — talk to self
    orch.run(instruction, to="agent") — talk to another agent
    orch.agent(name, ...) — register an agent
    """

    def __init__(self, api_url: str | None = None):
        self.api_url = api_url

    def agent(self, name: str, cwd: str | None = None, model: str | None = None,
              tools: list[str] | None = None, prompt: str | None = None) -> None:
        """Register an agent via API."""
        if not self.api_url:
            return
        data = {"name": name}
        if cwd: data["cwd"] = cwd
        if model: data["model"] = model
        if tools: data["tools"] = tools
        if prompt: data["prompt"] = prompt
        self._post_json("/agents", data)

    async def run(self, instruction: str, to: str = "self", schema: dict | None = None) -> str | dict:
        """Send instruction to an agent. Blocks until response."""
        if not self.api_url:
            raise RuntimeError("No API URL configured")

        max_attempts = 3 if schema else 1
        last_error = None

        for attempt in range(max_attempts):
            prompt = instruction
            if schema and attempt > 0:
                schema_desc = json.dumps(schema, indent=2)
                prompt += (f"\n\nYou MUST respond with ONLY a valid JSON object, no other text. "
                           f"Keys and types:\n{schema_desc}")

            form_data = urllib.parse.urlencode({
                "message": prompt,
                "source": "run",
            }).encode()

            req = urllib.request.Request(
                f"{self.api_url}/agents/{to}/message",
                data=form_data,
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

            print(f"[{to}] {result_text[:200]}", flush=True)

            if not schema:
                return result_text

            try:
                return _parse_json(result_text, schema)
            except ValueError as e:
                last_error = e
                print(f"[{to}] JSON parse failed (attempt {attempt + 1}/{max_attempts}): {e}", flush=True)

        raise last_error

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run() instead."""
        return await self.run(instruction, schema=schema)

    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Deprecated. Use run(instruction, to=...) instead."""
        return await self.run(instruction, to=to, schema=schema)

    def _post_json(self, path: str, data: dict) -> dict:
        """POST JSON to API, return parsed response."""
        encoded = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{self.api_url}{path}",
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())


Auto = Orchestrate  # deprecated alias
```

- [ ] **Step 2: Commit**

```bash
git add -f src/orchestrate/core.py
git commit -m "refactor: Orchestrate is now a pure HTTP client"
```

---

### Task 3: Update CLI — simplify env vars

**Files:**
- Modify: `src/orchestrate/cli.py`

- [ ] **Step 1: Simplify `_LazyOrchestrate`**

Only pass `api_url` — no more `session_id` or `program_name`:

```python
class _LazyOrchestrate:
    def __init__(self) -> None:
        self._real: object | None = None

    def _get(self) -> object:
        if self._real is None:
            from orchestrate.core import Orchestrate
            api_url = os.environ.get("ORCHESTRATE_API_URL")
            self._real = Orchestrate(api_url=api_url)
        return self._real

    def __getattr__(self, name: str):
        return getattr(self._get(), name)
```

- [ ] **Step 2: Simplify `_exec_program` — remove session_id/program_name env vars**

In the finally block, remove the `program-done` POST (no longer needed — agents live forever):

```python
def _exec_program(file_path: str, run_id: str, run_dir_path: str) -> None:
    run_dir = Path(run_dir_path)
    data = json.loads((run_dir / "run.json").read_text())

    try:
        spec = importlib.util.spec_from_file_location("_user_program", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        main_fn = getattr(module, "main", None)
        if main_fn is None or not inspect.iscoroutinefunction(main_fn):
            raise ValueError("No async def main() found in program")

        sig = inspect.signature(main_fn)
        params = list(sig.parameters.keys())

        orch = _LazyOrchestrate()

        if params:
            coro = main_fn(orch)
        else:
            coro = main_fn()

        asyncio.run(coro)

        data["status"] = "done"
    except Exception as exc:
        data["status"] = "error"
        data["error"] = str(exc)
    finally:
        (run_dir / "run.json").write_text(json.dumps(data))
```

- [ ] **Step 3: Simplify `cmd_run` — only pass `ORCHESTRATE_API_URL`**

```python
env = {**_os.environ, "ORCHESTRATE_API_URL": _os.environ.get("ORCHESTRATE_API_URL", "http://localhost:7777")}
```

Remove `ORCHESTRATE_SESSION_ID` and `ORCHESTRATE_PROGRAM_NAME` from env.

- [ ] **Step 4: Commit**

```bash
git add -f src/orchestrate/cli.py
git commit -m "refactor: CLI only passes API URL, no session/program env vars"
```

---

### Task 4: Pre-register "self" agent on server startup

**Files:**
- Modify: `api/server.py`

- [ ] **Step 1: Ensure "self" agent exists in AGENTS on startup**

The "self" agent is what `orch.run("do X")` (no `to`) posts to. It should always exist.

Add to the AGENTS dict initialization:

```python
AGENTS: dict[str, dict] = {
    "orchestrator": {
        "id": "orchestrator",
        "name": "Orchestrate Agent",
        "db_id": "default",
        "model": "claude-opus-4-6",
        "tools": ALL_TOOLS,
    },
    "self": {
        "id": "self",
        "name": "Self",
        "db_id": "default",
        "model": "claude-opus-4-6",
        "tools": ALL_TOOLS,
    },
}
```

The "self" agent shares config with "orchestrator" — both are the same model. When a program calls `orch.run("do X")`, it posts to `/agents/self/message`. The server processes it the same as any other agent.

- [ ] **Step 2: Commit**

```bash
git add -f api/server.py
git commit -m "feat: pre-register 'self' agent for orch.run() default"
```

---

### Task 5: Reinstall, test end-to-end

- [ ] **Step 1: Reinstall package**

```bash
pip install -e .
```

- [ ] **Step 2: Restart server**

```bash
kill $(lsof -ti:7777) 2>/dev/null
PYTHONPATH=/path/to/orchestrate uvicorn api.server:app --port 7777 --host 0.0.0.0
```

- [ ] **Step 3: API-level test — register agent and send message**

```bash
# Register a test agent
curl -s -X POST http://localhost:7777/agents \
  -H 'Content-Type: application/json' \
  -d '{"name": "test-agent"}'

# Send message to it
curl -s -X POST http://localhost:7777/agents/test-agent/message \
  -F 'message=say hi' -F 'source=run' --max-time 30

# Send to self
curl -s -X POST http://localhost:7777/agents/self/message \
  -F 'message=say hi' -F 'source=run' --max-time 30
```

- [ ] **Step 4: Program-level test**

Write a test program:
```python
# /tmp/test_api_first.py
import asyncio

async def main(orch):
    orch.agent("helper")
    result = await orch.run("say hello")
    print(f"Self said: {result[:50]}")
    result2 = await orch.run("say hi back", to="helper")
    print(f"Helper said: {result2[:50]}")

```

Run: `ORCHESTRATE_API_URL=http://localhost:7777 orchestrate-run run /tmp/test_api_first.py`

- [ ] **Step 5: UI test — send messages in browser**

Navigate to `http://localhost:3000/?agent=orchestrator&db_id=default`, send messages, verify bubbles appear correctly.

- [ ] **Step 6: Commit all fixes**

```bash
git add -A
git commit -m "test: verify API-first refactor end-to-end"
```

---

### Task 6: Update API docs

**Files:**
- Modify: `docs/api.md`

- [ ] **Step 1: Update docs to reflect new agent-keyed endpoints**

Replace session-centric docs with agent-centric:
- `POST /agents` — register with full config
- `POST /agents/{name}/message` — the core primitive
- `GET /agents/{name}/events` — SSE stream
- `DELETE /agents/{name}` — remove agent
- Keep backwards-compat note for `/sessions/{id}/message`

- [ ] **Step 2: Commit**

```bash
git add docs/api.md
git commit -m "docs: update API docs for agent-keyed architecture"
```
