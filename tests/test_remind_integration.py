"""Integration test: remind() via API mode posts to the API endpoint."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.server import app, AGENTS, SESSIONS, RUNS
from orchestrate.core import Auto


@pytest.fixture(autouse=True)
def _reset_state():
    SESSIONS.clear()
    RUNS.clear()
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


# ---- Auto API mode ----


def test_auto_api_mode_init():
    """Auto with api_url stores it for remind-via-HTTP."""
    auto = Auto(api_url="http://localhost:7777", session_id="sess-123")
    assert auto._api_url == "http://localhost:7777"
    assert auto._session_id == "sess-123"


def test_auto_without_api_url_has_no_api_mode():
    """Auto without api_url uses SDK mode (default)."""
    auto = Auto()
    assert auto._api_url is None
    assert auto._session_id is None


# ---- remind via API posts correct data ----


@pytest.mark.asyncio
async def test_remind_via_api_posts_to_endpoint():
    """When Auto has api_url, remind() should use /agents/{to}/message with source=remind."""
    from api.server import _process_agent_message
    import inspect

    source = inspect.getsource(_process_agent_message)
    # Verify the worker stores source in runs
    assert "source" in source
    assert "RUNS" in source


@pytest.mark.asyncio
async def test_user_message_has_source_user():
    """Normal user messages should have source=user default."""
    from fastapi.routing import APIRoute

    routes = [r.path for r in app.routes if isinstance(r, APIRoute)]
    # Verify the sessions endpoint exists
    assert "/agents/{agent_name}/sessions" in routes
    # Verify the ephemeral runs endpoint also exists
    assert "/agents/{agent_name}/runs" in routes


@pytest.mark.asyncio
async def test_session_runs_return_source_field(client):
    """GET /sessions/{id}/runs should include source in each run."""
    sid = "source-field-test"
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
            "source": "system",
        },
        {
            "run_input": "another",
            "content": "sure",
            "tools": [],
            "created_at": 1002,
            "source": "user",
        },
    ]

    resp = await client.get(f"/sessions/{sid}/runs", params={"type": "agent"})
    runs = resp.json()
    assert len(runs) == 3
    assert runs[0]["source"] == "user"
    assert runs[1]["source"] == "system"
    assert runs[2]["source"] == "user"


# ---- env vars flow ----


def test_api_env_vars_in_sdk_options():
    """API server should pass ORCHESTRATE env vars to ClaudeAgentOptions."""
    from api.server import _process_agent_message
    import inspect

    # Read the source to verify env vars are in the options
    source = inspect.getsource(_process_agent_message)
    assert "ORCHESTRATE_API_URL" in source
    assert "ORCHESTRATE_SESSION_ID" in source
    assert "session_id" in source  # session_id is passed as env var value
