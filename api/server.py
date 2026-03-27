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
        "model": {"name": "claude-opus-4-6", "model": "claude-opus-4-6", "provider": "anthropic"},
    }
}

SESSIONS: dict[str, dict] = {}
RUNS: dict[str, list] = {}
AUTOS: dict[str, Auto] = {}

# Per-session queue + worker + SSE output channel
SESSION_QUEUES: dict[str, asyncio.Queue] = {}       # input: messages to process
SESSION_SSE: dict[str, asyncio.Queue] = {}           # output: events pushed to stream
SESSION_WORKERS: dict[str, asyncio.Task] = {}

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


def _emit(session_id: str, event: dict):
    """Push event to the SSE output channel."""
    sse = SESSION_SSE.get(session_id)
    if sse:
        sse.put_nowait(json.dumps(event))


async def _process_message(message, source, agent_id, session_id, auto, run_id):
    """Run an Agent SDK query. Appends events directly to SESSION_EVENTS."""
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
            model=AGENTS.get(agent_id, {}).get("model", {}).get("model", "claude-opus-4-6"),
            effort="max",
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
                    if accumulated_text and not accumulated_text[-1].isspace() and block.text and not block.text[0].isspace():
                        accumulated_text += " "
                    accumulated_text += block.text
                    _emit(session_id, {
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
                    _emit(session_id, {
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

    return accumulated_text


async def _session_worker(session_id: str, agent_id: str):
    """Background worker: pulls from queue, processes sequentially."""
    queue = SESSION_QUEUES[session_id]
    auto = _get_or_create_auto(session_id, agent_id)
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

        # Tell UI this message left the queue
        _emit(session_id, {
            "event": "MessageDequeued",
            "content": item_message,
            "source": item_source,
            "session_id": session_id,
            "created_at": int(time.time()),
        })

        # Source marker — UI adds to main messages
        _emit(session_id, {
            "event": "RunContent",
            "content": item_message,
            "content_type": "text/plain",
            "source": item_source,
            "session_id": session_id,
            "run_id": item_run_id,
            "created_at": int(time.time()),
        })

        # Process sequentially
        response_text = await _process_message(
            item_message, item_source, agent_id, session_id, auto, item_run_id
        )

        if item_future and not item_future.done():
            item_future.set_result(response_text)

    _emit(session_id, {
        "event": "RunCompleted",
        "content": "",
        "content_type": "text/plain",
        "session_id": session_id,
        "run_id": "",
        "created_at": int(time.time()),
    })

    SESSION_WORKERS.pop(session_id, None)


def _ensure_worker(session_id: str, agent_id: str):
    """Start a background worker for this session if one isn't running."""
    if session_id not in SESSION_WORKERS or SESSION_WORKERS[session_id].done():
        if session_id not in SESSION_QUEUES:
            SESSION_QUEUES[session_id] = asyncio.Queue()
        if session_id not in SESSION_SSE:
            SESSION_SSE[session_id] = asyncio.Queue()
        SESSION_WORKERS[session_id] = asyncio.create_task(
            _session_worker(session_id, agent_id)
        )


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
    SESSION_SSE.pop(session_id, None)
    worker = SESSION_WORKERS.pop(session_id, None)
    if worker and not worker.done():
        worker.cancel()
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

    # Emit queued event so UI shows the message immediately
    _emit(session_id, {
        "event": "MessageQueued",
        "content": message,
        "source": source,
        "session_id": session_id,
        "created_at": int(time.time()),
    })

    result = await future
    return JSONResponse({"content": result, "status": "ok"})


@app.post("/sessions/{session_id}/program-done")
async def program_done(session_id: str):
    """Signal that the orchestrate program has finished."""
    if session_id in SESSION_QUEUES:
        await SESSION_QUEUES[session_id].put({"type": "done"})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Stream endpoint — just reads from the event log
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

    # Use first message as session name (truncated)
    if SESSIONS[session_id].get("session_name", "").startswith("Session "):
        SESSIONS[session_id]["session_name"] = message[:40] + " " + time.strftime("%H:%M")

    run_id = str(uuid.uuid4())
    now = int(time.time())

    # Ensure background worker is running
    _ensure_worker(session_id, agent_id)

    # Push the initial message to the queue (same as any other message)
    await SESSION_QUEUES[session_id].put({
        "message": message,
        "source": source,
    })

    async def generate():
        # RunStarted
        yield json.dumps({
            "event": "RunStarted",
            "session_id": session_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "content_type": "text/plain",
            "created_at": now,
        })

        # Read events pushed by the worker
        sse = SESSION_SSE.get(session_id)
        if not sse:
            return

        while True:
            event_str = await sse.get()
            yield event_str

    return StreamingResponse(generate(), media_type="text/event-stream")


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
