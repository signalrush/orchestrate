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


# Load OAuth token if available
try:
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(creds_path) and not os.environ.get("ANTHROPIC_API_KEY"):
        with open(creds_path) as f:
            creds = json.load(f)
        os.environ["ANTHROPIC_API_KEY"] = creds["claudeAiOauth"]["accessToken"]
except Exception:
    pass

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

app = FastAPI(title="orchestrate API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_TOOLS = ["Read", "Edit", "Write", "Bash", "Glob", "Grep", "Agent", "WebSearch", "WebFetch", "Skill"]

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

AGENTS: dict[str, dict] = {
    "orchestrator": {
        "id": "orchestrator",
        "name": "orchestrator",
        "model": "claude-opus-4-6",
        "cwd": os.getcwd(),
        "tools": ALL_TOOLS,
        "prompt": "",
    },
    # "self" is aliased to "orchestrator" in post_agent_message
}

SESSIONS: dict[str, dict] = {}
RUNS: dict[str, list] = {}

# Agent-keyed stores
AGENT_SESSIONS: dict[str, str] = {}        # name → session_id (resume id from SDK)
AGENT_QUEUES: dict[str, asyncio.Queue] = {}  # name → input Queue
AGENT_SSE: dict[str, asyncio.Queue] = {}    # name → output Queue
AGENT_WORKERS: dict[str, asyncio.Task] = {} # name → Task


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


def _emit_agent(agent_name: str, event: dict):
    """Push event to the agent's SSE output channel."""
    sse = AGENT_SSE.get(agent_name)
    if sse:
        sse.put_nowait(json.dumps(event))


async def _process_agent_message(message, source, agent_name, session_id, config, resume_id, run_id):
    """Run an Agent SDK query for a specific agent. Returns (accumulated_text, resume_id)."""
    accumulated_text = ""
    tools_used = []
    model = config.get("model", "claude-sonnet-4-6")
    cwd = config.get("cwd", os.getcwd())
    tools = config.get("tools", ALL_TOOLS)

    async for msg in query(
        prompt=message,
        options=ClaudeAgentOptions(
            allowed_tools=tools,
            permission_mode="bypassPermissions",
            model=model,
            effort="max",
            resume=resume_id,
            setting_sources=["user"],
            cwd=cwd,
            env={
                "ORCHESTRATE_API_URL": "http://localhost:7777",
                "ORCHESTRATE_SESSION_ID": session_id,
                "ORCHESTRATE_AGENT_NAME": agent_name,
            },
        ),
    ):
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

    # Store run
    RUNS.setdefault(session_id, []).append({
        "run_input": message,
        "content": accumulated_text,
        "tools": tools_used,
        "created_at": int(time.time()),
        "source": source,
    })
    if session_id in SESSIONS:
        SESSIONS[session_id]["updated_at"] = int(time.time())

    return accumulated_text, resume_id


async def _agent_worker(agent_name: str):
    """Background worker: pulls from agent's queue, processes sequentially."""
    queue = AGENT_QUEUES[agent_name]
    config = AGENTS.get(agent_name, {})
    resume_id = None

    try:
        while True:
            item = await queue.get()
            if item.get("type") == "done":
                continue

            item_source = item["source"]
            item_message = item["message"]
            item_future = item.get("future")
            item_run_id = str(uuid.uuid4())
            session_id = item.get("session_id", agent_name)

            # Tell UI this message left the queue
            _emit_agent(agent_name, {
                "event": "MessageDequeued",
                "content": item_message,
                "source": item_source,
                "session_id": session_id,
                "created_at": int(time.time()),
            })

            # Source marker — UI adds to main messages
            _emit_agent(agent_name, {
                "event": "RunContent",
                "content": item_message,
                "content_type": "text/plain",
                "source": item_source,
                "session_id": session_id,
                "run_id": item_run_id,
                "created_at": int(time.time()),
            })

            # Process sequentially, tracking resume_id locally
            try:
                response_text, new_resume_id = await _process_agent_message(
                    item_message, item_source, agent_name, session_id, config, resume_id, item_run_id
                )
                resume_id = new_resume_id
                if item_future and not item_future.done():
                    item_future.set_result(response_text)
            except Exception as e:
                _emit_agent(agent_name, {
                    "event": "RunError",
                    "content": str(e),
                    "agent_name": agent_name,
                    "session_id": session_id,
                    "created_at": int(time.time()),
                })
                if item_future and not item_future.done():
                    item_future.set_exception(e)
    except asyncio.CancelledError:
        return


def _ensure_agent_worker(agent_name: str):
    """Start a background worker for this agent if one isn't running."""
    if agent_name not in AGENT_WORKERS or AGENT_WORKERS[agent_name].done():
        if agent_name not in AGENT_QUEUES:
            AGENT_QUEUES[agent_name] = asyncio.Queue()
        if agent_name not in AGENT_SSE:
            AGENT_SSE[agent_name] = asyncio.Queue()
        AGENT_WORKERS[agent_name] = asyncio.create_task(
            _agent_worker(agent_name)
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


@app.post("/agents")
async def register_agent(request: Request):
    data = await request.json()
    agent_name = data.get("name", str(uuid.uuid4()))
    AGENTS[agent_name] = {
        "id": agent_name,
        "name": agent_name,
        "model": data.get("model", "claude-sonnet-4-6"),
        "cwd": data.get("cwd", os.getcwd()),
        "tools": data.get("tools", ALL_TOOLS),
        "prompt": data.get("prompt", ""),
    }
    return AGENTS[agent_name]


@app.post("/agents/{agent_name}/message")
async def post_agent_message(
    agent_name: str,
    message: str = Form(...),
    source: str = Form("user"),
    session_id: str = Form(""),
):
    """Push a message to the agent's queue. Blocks until processed, returns response."""
    # "self" is an alias for "orchestrator"
    if agent_name == "self":
        agent_name = "orchestrator"
    if agent_name not in AGENTS:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    _ensure_agent_worker(agent_name)

    if not session_id:
        session_id = agent_name

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    _emit_agent(agent_name, {
        "event": "MessageQueued",
        "content": message,
        "source": source,
        "session_id": session_id,
        "created_at": int(time.time()),
    })

    await AGENT_QUEUES[agent_name].put({
        "message": message,
        "source": source,
        "future": future,
        "session_id": session_id,
    })

    result = await future
    return JSONResponse({"content": result, "status": "ok"})


@app.get("/agents/{agent_name}/events")
async def agent_events(agent_name: str):
    """SSE stream reading from agent's SSE queue."""
    if agent_name not in AGENTS:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    _ensure_agent_worker(agent_name)

    async def generate():
        sse = AGENT_SSE.get(agent_name)
        if not sse:
            return
        while True:
            event_str = await sse.get()
            yield event_str

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.delete("/agents/{agent_name}")
async def delete_agent(agent_name: str):
    """Cleanup agent resources."""
    AGENTS.pop(agent_name, None)
    AGENT_QUEUES.pop(agent_name, None)
    AGENT_SSE.pop(agent_name, None)
    AGENT_SESSIONS.pop(agent_name, None)
    worker = AGENT_WORKERS.pop(agent_name, None)
    if worker and not worker.done():
        worker.cancel()
    return {"status": "deleted"}


@app.post("/agents/{agent_name}/runs")
async def run_agent(
    agent_name: str,
    message: str = Form(...),
    stream: str = Form("true"),
    session_id: str = Form(""),
    source: str = Form("user"),
):
    """UI entry point: create/resume a session and stream events."""
    if agent_name not in AGENTS:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    if not session_id:
        session_id = str(uuid.uuid4())
    _ensure_session(session_id, agent_name)

    # Use first message as session name (truncated)
    if SESSIONS[session_id].get("session_name", "").startswith("Session "):
        SESSIONS[session_id]["session_name"] = message[:40] + " " + time.strftime("%H:%M")

    run_id = str(uuid.uuid4())
    now = int(time.time())

    # Ensure worker is running for this agent
    _ensure_agent_worker(agent_name)

    # Push the initial message to the agent's queue
    await AGENT_QUEUES[agent_name].put({
        "message": message,
        "source": source,
        "session_id": session_id,
    })

    async def generate():
        yield json.dumps({
            "event": "RunStarted",
            "session_id": session_id,
            "run_id": run_id,
            "agent_name": agent_name,
            "content_type": "text/plain",
            "created_at": now,
        })

        sse = AGENT_SSE.get(agent_name)
        if not sse:
            return

        while True:
            event_str = await sse.get()
            yield event_str

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@app.get("/sessions")
async def list_sessions(
    session_type: str = Query("agent"),
    component_id: str = Query(""),
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
    session_type: str = Query("agent"),
):
    return RUNS.get(session_id, [])


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    SESSIONS.pop(session_id, None)
    RUNS.pop(session_id, None)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Backwards-compat: route session message to owning agent
# ---------------------------------------------------------------------------

@app.post("/sessions/{session_id}/message")
async def post_message(
    session_id: str,
    message: str = Form(...),
    source: str = Form("user"),
):
    """Backwards-compat: route to the agent that owns this session."""
    # Find the agent that owns this session
    agent_name = None
    session = SESSIONS.get(session_id)
    if session:
        agent_name = session.get("agent_id")

    if not agent_name or agent_name not in AGENTS:
        return JSONResponse({"error": "no agent found for session"}, status_code=400)

    _ensure_agent_worker(agent_name)

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    _emit_agent(agent_name, {
        "event": "MessageQueued",
        "content": message,
        "source": source,
        "session_id": session_id,
        "created_at": int(time.time()),
    })

    await AGENT_QUEUES[agent_name].put({
        "message": message,
        "source": source,
        "future": future,
        "session_id": session_id,
    })

    result = await future
    return JSONResponse({"content": result, "status": "ok"})
