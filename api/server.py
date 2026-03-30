"""REST API server bridging agent-ui to orchestrate.

Run: uvicorn api.server:app --port 7777
"""

import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
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

# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

_DB_PATH = Path.home() / ".orchestrate" / "orchestrate.db"


def _db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS agents (name TEXT PRIMARY KEY, resume_id TEXT, config TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT, session_id TEXT, source TEXT, input TEXT, content TEXT, tools TEXT, created_at INTEGER)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ephemeral_runs (
        id TEXT PRIMARY KEY,
        agent TEXT,
        task TEXT,
        schema TEXT,
        data TEXT,
        text TEXT,
        summary TEXT,
        messages TEXT,
        created_at INTEGER,
        completed_at INTEGER
    )"""
    )
    return conn

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALL_TOOLS = ["Read", "Edit", "Write", "Bash", "Glob", "Grep", "Agent", "WebSearch", "WebFetch", "Skill"]


@app.on_event("startup")
async def load_persisted_agents():
    conn = _db()
    for row in conn.execute("SELECT name, resume_id, config FROM agents").fetchall():
        name = row["name"]
        config = json.loads(row["config"]) if row["config"] else {}
        if name not in AGENTS:
            AGENTS[name] = config
            AGENTS[name]["resume_id"] = row["resume_id"]
    # Rebuild sessions from persisted runs
    for row in conn.execute("SELECT DISTINCT agent_name, session_id FROM runs").fetchall():
        sid = row["session_id"]
        if sid and sid not in SESSIONS:
            SESSIONS[sid] = {
                "session_id": sid,
                "session_name": row["agent_name"],
                "agent_id": "orchestrator",
                "created_at": 0,
                "updated_at": 0,
            }
            ts = conn.execute("SELECT MIN(created_at) as first, MAX(created_at) as last FROM runs WHERE session_id = ?", (sid,)).fetchone()
            if ts:
                SESSIONS[sid]["created_at"] = ts["first"] or 0
                SESSIONS[sid]["updated_at"] = ts["last"] or 0
    conn.close()

    # Register default orchestrator agent if not loaded from DB
    if "orchestrator" not in AGENTS:
        AGENTS["orchestrator"] = {
            "id": "orchestrator",
            "name": "orchestrator",
            "model": "claude-opus-4-6",
            "cwd": os.getcwd(),
            "tools": ALL_TOOLS,
            "prompt": "",
        }
        conn = _db()
        conn.execute("INSERT OR REPLACE INTO agents (name, resume_id, config) VALUES (?, ?, ?)",
                     ("orchestrator", None, json.dumps(AGENTS["orchestrator"])))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

AGENTS: dict[str, dict] = {}
# "self" is aliased to "orchestrator" in post_agent_message

SESSIONS: dict[str, dict] = {}
RUNS: dict[str, list] = {}

# Agent-keyed stores
AGENT_QUEUES: dict[str, asyncio.Queue] = {}  # name → input Queue
TEAM_SSE: asyncio.Queue = asyncio.Queue()   # single team stream
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


def _emit(event: dict):
    """Push event to the team SSE stream."""
    TEAM_SSE.put_nowait(json.dumps(event))


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
                    _emit({
                        "event": "RunContent",
                        "content": accumulated_text,
                        "content_type": "text/plain",
                        "agent_name": agent_name,
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
                    _emit({
                        "event": "ToolCallStarted",
                        "tools": [tool_record],
                        "content_type": "text/plain",
                        "agent_name": agent_name,
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
    conn = _db()
    conn.execute("INSERT INTO runs (agent_name, session_id, source, input, content, tools, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (agent_name, session_id, source, message, accumulated_text, json.dumps(tools_used), int(time.time())))
    conn.commit()
    conn.close()
    if session_id in SESSIONS:
        SESSIONS[session_id]["updated_at"] = int(time.time())

    return accumulated_text, resume_id


async def _agent_worker(agent_name: str):
    """Background worker: pulls from agent's queue, processes sequentially."""
    queue = AGENT_QUEUES[agent_name]
    config = AGENTS.get(agent_name, {})
    resume_id = config.get("resume_id")

    try:
        while True:
            item = await queue.get()
            if item.get("type") == "done":
                continue

            item_source = item["source"]
            item_message = item["message"]
            item_future = item.get("future")
            item_run_id = str(uuid.uuid4())
            session_id = item.get("session_id") or config.get("session_id", agent_name)

            # Tell UI this message left the queue
            _emit({
                "event": "MessageDequeued",
                "content": item_message,
                "source": item_source,
                "agent_name": agent_name,
                "session_id": session_id,
                "created_at": int(time.time()),
            })

            # Source marker — UI adds to main messages
            _emit({
                "event": "RunContent",
                "content": item_message,
                "content_type": "text/plain",
                "source": item_source,
                "agent_name": agent_name,
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
                conn = _db()
                conn.execute("INSERT OR REPLACE INTO agents (name, resume_id, config) VALUES (?, ?, ?)",
                             (agent_name, resume_id, json.dumps(config)))
                conn.commit()
                conn.close()
                if item_future and not item_future.done():
                    item_future.set_result(response_text)
            except Exception as e:
                _emit({
                    "event": "RunError",
                    "content": str(e),
                    "agent_name": agent_name,
                    "session_id": session_id,
                    "created_at": int(time.time()),
                })
                if item_future and not item_future.done():
                    item_future.set_exception(e)

            # No RunCompleted — stream stays open for program reminds
    except asyncio.CancelledError:
        return


def _ensure_agent_worker(agent_name: str):
    """Start a background worker for this agent if one isn't running."""
    if agent_name not in AGENT_WORKERS or AGENT_WORKERS[agent_name].done():
        if agent_name not in AGENT_QUEUES:
            AGENT_QUEUES[agent_name] = asyncio.Queue()
        # Create session with UUID (if agent doesn't have one yet)
        if "session_id" not in AGENTS.get(agent_name, {}):
            sid = str(uuid.uuid4())
            _ensure_session(sid, "orchestrator")
            SESSIONS[sid]["session_name"] = agent_name
            if agent_name in AGENTS:
                AGENTS[agent_name]["session_id"] = sid
        AGENT_WORKERS[agent_name] = asyncio.create_task(
            _agent_worker(agent_name)
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/teams")
async def list_teams():
    return []


@app.get("/teams/default/events")
async def team_events():
    """Persistent SSE stream. All agent events flow here tagged with agent_name."""
    async def generate():
        while True:
            event_str = await TEAM_SSE.get()
            yield event_str
    return StreamingResponse(generate(), media_type="text/event-stream")


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
        "db_id": agent_name,
        "model": data.get("model", "claude-sonnet-4-6"),
        "cwd": data.get("cwd", os.getcwd()),
        "tools": data.get("tools", ALL_TOOLS),
        "prompt": data.get("prompt", ""),
    }
    conn = _db()
    conn.execute("INSERT OR REPLACE INTO agents (name, resume_id, config) VALUES (?, ?, ?)",
                 (agent_name, None, json.dumps(AGENTS[agent_name])))
    conn.commit()
    conn.close()
    # Create worker + session so it appears in sidebar immediately
    _ensure_agent_worker(agent_name)
    # Notify the team SSE stream so UI refreshes sidebar
    _emit({
        "event": "AgentRegistered",
        "agent_name": agent_name,
        "session_id": AGENTS[agent_name].get("session_id", ""),
        "created_at": int(time.time()),
    })
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
        session_id = AGENTS[agent_name].get("session_id", agent_name)

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    _emit({
        "event": "MessageQueued",
        "content": message,
        "source": source,
        "agent_name": agent_name,
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
    """Redirect to team events stream."""
    return await team_events()


@app.delete("/agents/{agent_name}")
async def delete_agent(agent_name: str):
    """Cleanup agent resources."""
    AGENTS.pop(agent_name, None)
    AGENT_QUEUES.pop(agent_name, None)
    worker = AGENT_WORKERS.pop(agent_name, None)
    if worker and not worker.done():
        worker.cancel()
    conn = _db()
    conn.execute("DELETE FROM agents WHERE name = ?", (agent_name,))
    conn.execute("DELETE FROM runs WHERE agent_name = ?", (agent_name,))
    conn.commit()
    conn.close()
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
        # Reuse agent's existing session if it has one
        session_id = AGENTS[agent_name].get("session_id") or str(uuid.uuid4())
    _ensure_session(session_id, agent_name)
    AGENTS[agent_name]["session_id"] = session_id

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

    # Emit RunStarted to team stream
    _emit({
        "event": "RunStarted",
        "session_id": session_id,
        "run_id": run_id,
        "agent_name": agent_name,
        "content_type": "text/plain",
        "created_at": now,
    })

    # Return the team SSE stream (same as GET /teams/default/events)
    return await team_events()


# ---------------------------------------------------------------------------
# Ephemeral run endpoints (L1 Agent Runtime)
# ---------------------------------------------------------------------------

HAIKU_MODEL = "claude-haiku-4-5-20251001"
EPHEMERAL_TASKS: dict[str, asyncio.Task] = {}  # run_id → Task


def _extract_last_json(text: str) -> str | None:
    """Extract the last JSON object or array from text, ignoring surrounding prose."""
    last_match = None
    pos = len(text) - 1
    while pos >= 0:
        if text[pos] in ("}", "]"):
            close_char = text[pos]
            open_char = "{" if close_char == "}" else "["
            depth = 0
            for i in range(pos, -1, -1):
                if text[i] == close_char:
                    depth += 1
                elif text[i] == open_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[i : pos + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break
            pos -= 1
        else:
            pos -= 1
    return last_match


async def _summarize(text: str) -> str:
    """Generate a one-line summary via Haiku direct API call."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": HAIKU_MODEL,
                "max_tokens": 128,
                "messages": [
                    {
                        "role": "user",
                        "content": f"Summarize in one short sentence:\n\n{text[:4000]}",
                    }
                ],
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["content"][0]["text"]
    return text[:120]


async def _execute_ephemeral_run(
    run_id: str,
    agent_name: str,
    task: str,
    schema: dict[str, Any] | None,
    context_ids: list[str] | None,
    config: dict[str, Any],
) -> None:
    """Execute an ephemeral task inline (not via worker queue)."""
    now = int(time.time())

    try:
        model = config.get("model", "claude-sonnet-4-6")
        cwd = config.get("cwd", os.getcwd())
        tools = config.get("tools", ALL_TOOLS)
        messages: list[dict[str, Any]] = []

        # Prepend context from previous runs if requested
        prompt = task
        if context_ids:
            conn = _db()
            for cid in context_ids:
                row = conn.execute(
                    "SELECT summary, text FROM ephemeral_runs WHERE id = ?", (cid,)
                ).fetchone()
                if row:
                    ctx = row["summary"] or row["text"] or ""
                    if ctx:
                        prompt = f"[Context from run {cid}]: {ctx}\n\n{prompt}"
            conn.close()

        # Schema instruction
        if schema:
            prompt += f"\n\nYou MUST respond with valid JSON matching this schema: {json.dumps(schema)}"

        accumulated_text = ""
        max_attempts = 3 if schema else 1

        for attempt in range(max_attempts):
            accumulated_text = ""
            current_messages: list[dict[str, Any]] = []

            async for msg in query(
                prompt=(
                    prompt
                    if attempt == 0
                    else f"Your previous response was not valid JSON matching the schema. Try again.\n\n{prompt}"
                ),
                options=ClaudeAgentOptions(
                    allowed_tools=tools,
                    permission_mode="bypassPermissions",
                    model=model,
                    effort="max",
                    resume=None,  # always fresh — never resume for ephemeral runs
                    setting_sources=["user"],
                    cwd=cwd,
                ),
            ):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if hasattr(block, "text"):
                            if (
                                accumulated_text
                                and not accumulated_text[-1].isspace()
                                and block.text
                                and not block.text[0].isspace()
                            ):
                                accumulated_text += " "
                            accumulated_text += block.text
                            _emit({
                                "event": "RunContent",
                                "content": accumulated_text,
                                "content_type": "text/plain",
                                "agent_name": agent_name,
                                "run_id": run_id,
                                "created_at": int(time.time()),
                            })
                            current_messages.append(
                                {"role": "assistant", "text": block.text}
                            )
                        elif hasattr(block, "name"):
                            tool_record = {
                                "role": "tool",
                                "tool_name": block.name,
                                "tool_args": getattr(block, "input", {}),
                            }
                            _emit({
                                "event": "ToolCallStarted",
                                "tools": [tool_record],
                                "agent_name": agent_name,
                                "run_id": run_id,
                                "created_at": int(time.time()),
                            })
                            current_messages.append(tool_record)

            messages.extend(current_messages)

            # Validate against schema if provided
            if schema:
                # Extract last JSON from accumulated text (may contain reasoning)
                json_str = _extract_last_json(accumulated_text)
                if json_str:
                    try:
                        parsed = json.loads(json_str)
                        if isinstance(schema, dict) and "properties" in schema:
                            required = set(
                                schema.get("required", schema["properties"].keys())
                            )
                            if required - set(parsed.keys()):
                                if attempt < max_attempts - 1:
                                    continue
                        break  # valid
                    except json.JSONDecodeError:
                        if attempt < max_attempts - 1:
                            continue
                elif attempt < max_attempts - 1:
                    continue
            else:
                break

        # Parse structured data if schema was provided
        data: str | None = None
        if schema:
            json_str = _extract_last_json(accumulated_text)
            if json_str:
                data = json_str

        # Summarize
        summary = await _summarize(accumulated_text) if accumulated_text else ""

        # Store
        completed_at = int(time.time())
        conn = _db()
        conn.execute(
            "INSERT INTO ephemeral_runs (id, agent, task, schema, data, text, summary, messages, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                agent_name,
                task,
                json.dumps(schema) if schema else None,
                data,
                accumulated_text,
                summary,
                json.dumps(messages),
                now,
                completed_at,
            ),
        )
        conn.commit()
        conn.close()

        _emit({
            "event": "EphemeralRunCompleted",
            "run_id": run_id,
            "agent_name": agent_name,
            "summary": summary,
            "created_at": completed_at,
        })

    except Exception as e:
        # Catch-all: emit error and write failed state to DB
        _emit({
            "event": "RunError",
            "content": str(e),
            "agent_name": agent_name,
            "run_id": run_id,
            "created_at": int(time.time()),
        })
        conn = _db()
        conn.execute(
            "INSERT OR REPLACE INTO ephemeral_runs (id, agent, task, schema, data, text, summary, messages, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                agent_name,
                task,
                None,
                None,
                "",
                f"error: {e}",
                "[]",
                now,
                int(time.time()),
            ),
        )
        conn.commit()
        conn.close()
    finally:
        EPHEMERAL_TASKS.pop(run_id, None)


@app.post("/agents/{agent_name}/ephemeral")
async def ephemeral_run(agent_name: str, request: Request):
    """Ephemeral task execution — fire-and-forget, no persistent session."""
    if agent_name not in AGENTS:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    body = await request.json()
    task = body.get("task")
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)

    schema = body.get("schema")
    context = body.get("context")
    run_id = str(uuid.uuid4())
    config = AGENTS[agent_name]

    # Store task reference to prevent GC
    t = asyncio.create_task(
        _execute_ephemeral_run(run_id, agent_name, task, schema, context, config)
    )
    EPHEMERAL_TASKS[run_id] = t

    # Exception callback: emit RunError for any exception that escapes the coroutine
    def _on_task_done(fut: asyncio.Task, _run_id=run_id, _agent=agent_name):
        if not fut.cancelled() and fut.exception():
            _emit({
                "event": "RunError",
                "content": str(fut.exception()),
                "agent_name": _agent,
                "run_id": _run_id,
                "created_at": int(time.time()),
            })

    t.add_done_callback(_on_task_done)

    return JSONResponse({"run_id": run_id, "status": "ok"})


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Retrieve a stored ephemeral run result."""
    conn = _db()
    row = conn.execute(
        "SELECT * FROM ephemeral_runs WHERE id = ?", (run_id,)
    ).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"error": "run not found"}, status_code=404)
    return {
        "id": row["id"],
        "agent": row["agent"],
        "task": row["task"],
        "schema": json.loads(row["schema"]) if row["schema"] else None,
        "data": json.loads(row["data"]) if row["data"] else None,
        "text": row["text"],
        "summary": row["summary"],
        "messages": json.loads(row["messages"]) if row["messages"] else [],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


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
    runs = RUNS.get(session_id, [])
    if not runs:
        conn = _db()
        rows = conn.execute("SELECT * FROM runs WHERE session_id = ? ORDER BY created_at", (session_id,)).fetchall()
        conn.close()
        runs = [{
            "run_input": r["input"],
            "content": r["content"],
            "tools": json.loads(r["tools"]) if r["tools"] else [],
            "created_at": r["created_at"],
            "source": r["source"],
        } for r in rows]
    return runs


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

    _emit({
        "event": "MessageQueued",
        "content": message,
        "source": source,
        "agent_name": agent_name,
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
