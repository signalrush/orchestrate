"""Tests for the REST API server (agent-ui compatibility)."""

import pytest
from httpx import ASGITransport, AsyncClient

from api.server import app, AGENTS, SESSIONS, RUNS


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset in-memory stores between tests."""
    SESSIONS.clear()
    RUNS.clear()
    # Restore default agent
    AGENTS.clear()
    AGENTS["orchestrator"] = {
        "id": "orchestrator",
        "name": "Orchestrate Agent",
        "db_id": "default",
        "model": {
            "name": "claude-sonnet-4-6",
            "model": "claude-sonnet-4-6",
            "provider": "anthropic",
        },
    }


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---- agent-ui compatibility: agent response shape ----


@pytest.mark.asyncio
async def test_agents_have_db_id(client):
    """agent-ui requires db_id on agents to enable session switching.

    Without db_id, the UI guard `if (!dbId) return` blocks session
    loading, making it impossible to switch back to previous chats.
    """
    resp = await client.get("/agents")
    assert resp.status_code == 200
    agents = resp.json()
    for agent in agents:
        assert "db_id" in agent, f"agent {agent['id']} missing db_id"
        assert agent["db_id"], f"agent {agent['id']} has empty db_id"


@pytest.mark.asyncio
async def test_agents_have_required_fields(client):
    """agent-ui expects id, name, model on every agent."""
    resp = await client.get("/agents")
    agents = resp.json()
    for agent in agents:
        assert "id" in agent
        assert "name" in agent
        assert "model" in agent
        assert "model" in agent["model"]
        assert "provider" in agent["model"]


@pytest.mark.asyncio
async def test_registered_agent_has_name(client):
    """Dynamically registered agents must include name and id."""
    resp = await client.post("/agents", json={"name": "test-agent"})
    assert resp.status_code == 200
    agent = resp.json()
    assert agent["name"] == "test-agent"
    assert agent["id"] == "test-agent"


# ---- session endpoints ----


@pytest.mark.asyncio
async def test_sessions_returns_data_wrapper(client):
    """agent-ui expects {data: [...]} from GET /sessions."""
    resp = await client.get("/sessions", params={"type": "agent"})
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert isinstance(body["data"], list)


@pytest.mark.asyncio
async def test_session_runs_returns_array(client):
    """agent-ui expects a flat array from GET /sessions/{id}/runs."""
    resp = await client.get("/sessions/nonexistent/runs", params={"type": "agent"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_session_runs_have_run_input_and_content(client):
    """agent-ui reconstructs messages from run_input + content fields."""
    # Seed a session with a run
    sid = "test-session"
    SESSIONS[sid] = {
        "session_id": sid,
        "session_name": "Test",
        "agent_id": "orchestrator",
        "created_at": 1000,
        "updated_at": 1000,
    }
    RUNS[sid] = [
        {"run_input": "hello", "content": "hi there", "tools": [], "created_at": 1000},
    ]

    resp = await client.get(f"/sessions/{sid}/runs", params={"type": "agent"})
    runs = resp.json()
    assert len(runs) == 1
    assert "run_input" in runs[0]
    assert "content" in runs[0]
    assert "created_at" in runs[0]


# ---- health / teams ----


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_teams_returns_empty_list(client):
    resp = await client.get("/teams")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_delete_session(client):
    sid = "to-delete"
    SESSIONS[sid] = {"session_id": sid}
    RUNS[sid] = []
    resp = await client.delete(f"/sessions/{sid}", params={"db_id": ""})
    assert resp.status_code == 200
    assert sid not in SESSIONS
    assert sid not in RUNS


@pytest.mark.asyncio
async def test_run_stores_source_field(client):
    """Run records should include source field."""
    sid = "test-source"
    SESSIONS[sid] = {
        "session_id": sid,
        "session_name": "Test",
        "agent_id": "orchestrator",
        "created_at": 1000,
        "updated_at": 1000,
    }
    RUNS[sid] = [
        {
            "run_input": "hello",
            "content": "hi",
            "tools": [],
            "created_at": 1000,
            "source": "user",
        },
        {
            "run_input": "remind msg",
            "content": "ok",
            "tools": [],
            "created_at": 1001,
            "source": "remind",
        },
    ]
    resp = await client.get(f"/sessions/{sid}/runs", params={"type": "agent"})
    runs = resp.json()
    assert runs[0]["source"] == "user"
    assert runs[1]["source"] == "remind"


# ---- ephemeral run endpoints ----


@pytest.mark.asyncio
async def test_ephemeral_run_requires_task(client):
    """POST /agents/{name}/runs requires a task field."""
    resp = await client.post("/agents/orchestrator/runs", json={})
    assert resp.status_code == 400
    assert "task" in resp.json()["error"]


@pytest.mark.asyncio
async def test_ephemeral_run_agent_not_found(client):
    """POST /agents/{name}/runs returns 404 for unknown agent."""
    resp = await client.post("/agents/nonexistent/runs", json={"task": "hello"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    """GET /runs/{id} returns 404 for unknown run."""
    resp = await client.get("/runs/nonexistent-id")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]


@pytest.mark.asyncio
async def test_session_endpoint_renamed(client):
    """POST /agents/{name}/sessions should exist (renamed from /runs)."""
    from fastapi.routing import APIRoute

    routes = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert "/agents/{agent_name}/sessions" in routes
    assert "/agents/{agent_name}/runs" in routes  # new ephemeral endpoint


# ---- context endpoints ----


@pytest.mark.asyncio
async def test_save_and_search_context(client):
    """POST /context saves entry, GET /context retrieves it."""
    resp = await client.post(
        "/context", json={"text": "hello world", "tags": ["test"], "agent": "bot"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "created_at" in body

    resp = await client.get("/context", params={"q": "hello"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert data[0]["text"] == "hello world"
    assert data[0]["summary"] == "hello world"
    assert data[0]["agent"] == "bot"


@pytest.mark.asyncio
async def test_context_search_by_tags(client):
    await client.post(
        "/context", json={"text": "tagged entry", "tags": ["alpha", "beta"]}
    )
    resp = await client.get("/context", params={"tags": "alpha"})
    data = resp.json()["data"]
    assert len(data) >= 1
    assert data[0]["text"] == "tagged entry"


@pytest.mark.asyncio
async def test_context_search_by_agent(client):
    await client.post("/context", json={"text": "agent entry", "agent": "worker1"})
    resp = await client.get("/context", params={"agent": "worker1"})
    data = resp.json()["data"]
    assert len(data) >= 1
    assert data[0]["agent"] == "worker1"


@pytest.mark.asyncio
async def test_context_pin_unpin(client):
    resp = await client.post("/context", json={"text": "pin me"})
    entry_id = resp.json()["id"]

    resp = await client.post(f"/context/{entry_id}/pin")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Verify pinned
    resp = await client.get("/context", params={"q": "pin me"})
    assert resp.json()["data"][0]["pinned"] == 1

    resp = await client.delete(f"/context/{entry_id}/pin")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Verify unpinned
    resp = await client.get("/context", params={"q": "pin me"})
    assert resp.json()["data"][0]["pinned"] == 0


@pytest.mark.asyncio
async def test_context_summary_truncated(client):
    long_text = "x" * 500
    resp = await client.post("/context", json={"text": long_text})
    entry_id = resp.json()["id"]
    resp = await client.get("/context", params={"q": "xxx"})
    data = resp.json()["data"]
    assert len(data[0]["summary"]) == 200


@pytest.mark.asyncio
async def test_context_empty_search(client):
    resp = await client.get("/context")
    assert resp.status_code == 200
    assert "data" in resp.json()


# ---- L3 glue tests ----


@pytest.mark.asyncio
async def test_ephemeral_run_accepts_context_parameter(client):
    """POST /agents/{name}/runs accepts optional context list."""
    resp = await client.post(
        "/agents/orchestrator/runs",
        json={"task": "hello", "context": ["run-id-1", "run-id-2"]},
    )
    # Returns run_id immediately (background task), not 400
    assert resp.status_code == 200
    assert "run_id" in resp.json()


@pytest.mark.asyncio
async def test_ephemeral_run_returns_run_id(client):
    """POST /agents/{name}/runs returns a run_id for polling."""
    resp = await client.post("/agents/orchestrator/runs", json={"task": "do something"})
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_context_auto_save_fields(client):
    """POST /context stores all required L3 fields including run_id."""
    resp = await client.post(
        "/context",
        json={
            "text": "result text",
            "agent": "worker",
            "task": "do work",
            "run_id": "abc-123",
        },
    )
    assert resp.status_code == 200
    entry_id = resp.json()["id"]

    resp = await client.get("/context", params={"q": "result text"})
    data = resp.json()["data"]
    assert len(data) >= 1
    entry = data[0]
    assert entry["agent"] == "worker"
    assert entry["task"] == "do work"
    assert entry["run_id"] == "abc-123"
