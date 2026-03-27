"""REST API server bridging agent-ui to orchestrate.

Run: uvicorn api.server:app --port 7777
"""

import asyncio
import json
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Form, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, Response

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

# Per-session event queues: remind() pushes events here, the active stream yields them
SESSION_QUEUES: dict[str, asyncio.Queue] = {}

# Timeout waiting for first remind event after agent turn (seconds)
WAIT_FOR_PROGRAM_TIMEOUT = 15
# Timeout between remind events once started (seconds)
BETWEEN_REMIND_TIMEOUT = 120


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


async def _run_agent_query(message, agent_id, session_id, auto, queue, run_id, source):
    """Run an Agent SDK query and yield streaming events. Pushes events to queue too."""
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

    return accumulated_text, tools_used


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
    if session_id in AUTOS:
        del AUTOS[session_id]
    return {"status": "deleted"}


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

    # -------------------------------------------------------------------
    # REMIND MODE: push events to the active stream's queue
    # -------------------------------------------------------------------
    if source == "remind" and session_id in SESSION_QUEUES:
        queue = SESSION_QUEUES[session_id]
        auto = _get_or_create_auto(session_id, agent_id)
        run_id = str(uuid.uuid4())

        # Push remind instruction event
        await queue.put(json.dumps({
            "event": "RunContent",
            "content": message,
            "content_type": "text/plain",
            "source": "remind",
            "session_id": session_id,
            "run_id": run_id,
            "created_at": int(time.time()),
        }))

        # Run agent query — push response events to queue
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
            ),
        ):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if hasattr(block, "text"):
                        accumulated_text += block.text
                        await queue.put(json.dumps({
                            "event": "RunContent",
                            "content": accumulated_text,
                            "content_type": "text/plain",
                            "session_id": session_id,
                            "run_id": run_id,
                            "created_at": int(time.time()),
                        }))
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
                        await queue.put(json.dumps({
                            "event": "ToolCallStarted",
                            "tools": [tool_record],
                            "content_type": "text/plain",
                            "session_id": session_id,
                            "run_id": run_id,
                            "created_at": int(time.time()),
                        }))
            elif isinstance(msg, ResultMessage):
                if "self" not in auto._sessions:
                    auto.agent("self")
                auto._sessions["self"]["session_id"] = msg.session_id

        # Store remind run
        RUNS.setdefault(session_id, []).append({
            "run_input": message,
            "content": accumulated_text,
            "tools": tools_used,
            "created_at": int(time.time()),
            "source": "remind",
        })
        SESSIONS[session_id]["updated_at"] = int(time.time())

        return JSONResponse({"content": accumulated_text, "status": "ok"})

    # -------------------------------------------------------------------
    # NORMAL MODE: stream response directly to client
    # -------------------------------------------------------------------
    auto = _get_or_create_auto(session_id, agent_id)
    run_id = str(uuid.uuid4())
    now = int(time.time())

    async def generate():
        # Create queue for this session so remind() can push events
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
            "source": source,
        })

        accumulated_text = ""
        tools_used = []

        try:
            # Stream the agent's response to the user's message
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

            # Store the initial run
            RUNS.setdefault(session_id, []).append({
                "run_input": message,
                "content": accumulated_text,
                "tools": tools_used,
                "created_at": now,
                "source": source,
            })
            SESSIONS[session_id]["updated_at"] = int(time.time())

            # -----------------------------------------------------------
            # WAIT FOR REMIND EVENTS from background program
            # -----------------------------------------------------------
            # After the agent's turn, wait for remind events pushed by
            # the background orchestrate program. If nothing comes within
            # WAIT_FOR_PROGRAM_TIMEOUT, close the stream.
            try:
                while True:
                    timeout = WAIT_FOR_PROGRAM_TIMEOUT if not tools_used else WAIT_FOR_PROGRAM_TIMEOUT
                    event_str = await asyncio.wait_for(queue.get(), timeout=timeout)
                    if event_str is None:  # done signal
                        break
                    yield event_str
                    # After first event, use longer timeout between events
                    while True:
                        try:
                            event_str = await asyncio.wait_for(queue.get(), timeout=BETWEEN_REMIND_TIMEOUT)
                            if event_str is None:
                                break
                            yield event_str
                        except asyncio.TimeoutError:
                            break
                    break
            except asyncio.TimeoutError:
                pass  # No program events, close stream normally

            # RunCompleted
            yield json.dumps({
                "event": "RunCompleted",
                "content": accumulated_text,
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
            # Cleanup queue
            SESSION_QUEUES.pop(session_id, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/sessions/{session_id}/program-done")
async def program_done(session_id: str):
    """Signal that the orchestrate program has finished."""
    if session_id in SESSION_QUEUES:
        await SESSION_QUEUES[session_id].put(None)  # done signal
    return {"status": "ok"}


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
