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

# agent_id -> agent config
AGENTS: dict[str, dict] = {
    "orchestrator": {
        "id": "orchestrator",
        "name": "Orchestrate Agent",
        "db_id": "default",
        "model": {"name": "claude-sonnet-4-6", "model": "claude-sonnet-4-6", "provider": "anthropic"},
    }
}

# session_id -> session state
SESSIONS: dict[str, dict] = {}

# session_id -> list of run records (message history)
RUNS: dict[str, list] = {}

# session_id -> Auto instance (for session accumulation)
AUTOS: dict[str, Auto] = {}


def _get_or_create_auto(session_id: str, agent_id: str) -> Auto:
    """Get or create an Auto instance for a session."""
    if session_id not in AUTOS:
        agent_config = AGENTS.get(agent_id, AGENTS["orchestrator"])
        model = agent_config.get("model", {}).get("model", "claude-sonnet-4-6")
        AUTOS[session_id] = Auto(model=model)
    return AUTOS[session_id]


def _ensure_session(session_id: str, agent_id: str) -> dict:
    """Ensure a session exists in the store."""
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
    # Create or reuse session
    if not session_id:
        session_id = str(uuid.uuid4())
    _ensure_session(session_id, agent_id)

    auto = _get_or_create_auto(session_id, agent_id)
    run_id = str(uuid.uuid4())
    now = int(time.time())

    async def generate():
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
                            yield json.dumps({
                                "event": "RunContent",
                                "content": accumulated_text,
                                "content_type": "text/plain",
                                "session_id": session_id,
                                "run_id": run_id,
                                "created_at": int(time.time()),
                            })
                        elif hasattr(block, "name"):
                            # Tool call
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
                    # Update Auto's session for accumulation
                    if "self" not in auto._sessions:
                        auto.agent("self")
                    auto._sessions["self"]["session_id"] = msg.session_id

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

        # Store run for history
        RUNS.setdefault(session_id, []).append({
            "run_input": message,
            "content": accumulated_text,
            "tools": tools_used,
            "created_at": now,
            "source": source,
        })
        SESSIONS[session_id]["updated_at"] = int(time.time())

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Dynamic agent registration
# ---------------------------------------------------------------------------

@app.post("/agents")
async def register_agent(request: Request):
    """Register a new agent dynamically."""
    data = await request.json()
    agent_id = data.get("id", str(uuid.uuid4()))
    AGENTS[agent_id] = {
        "id": agent_id,
        "name": data.get("name", agent_id),
        "db_id": data.get("db_id", "default"),
        "model": data.get("model", {"name": "claude-sonnet-4-6", "model": "claude-sonnet-4-6", "provider": "anthropic"}),
    }
    return AGENTS[agent_id]
