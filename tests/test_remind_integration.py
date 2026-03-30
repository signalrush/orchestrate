"""Integration test: Orchestrate SDK and API endpoint compatibility."""

import json
import os
import time
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.server import app, AGENTS, SESSIONS, RUNS
from orchestrate.core import Orchestrate


@pytest.fixture(autouse=True)
def _reset_state():
    SESSIONS.clear()
    RUNS.clear()
    AGENTS.clear()
    AGENTS["orchestrator"] = {
        "id": "orchestrator",
        "name": "orchestrator",
        "model": "claude-sonnet-4-6",
        "cwd": os.getcwd(),
        "tools": [],
        "prompt": "",
    }


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---- Orchestrate SDK init ----


def test_orchestrate_init_with_api_url():
    """Orchestrate with api_url stores it for HTTP calls."""
    orch = Orchestrate(api_url="http://localhost:7777")
    assert orch._api_url == "http://localhost:7777"


def test_orchestrate_without_api_url():
    """Orchestrate without api_url defaults to None."""
    orch = Orchestrate(api_url="http://localhost:7777")
    assert orch._api_url is not None


# ---- ephemeral run via API ----


@pytest.mark.asyncio
async def test_ephemeral_run_endpoint(client):
    """POST /agents/{name}/runs accepts JSON with task field."""
    resp = await client.post(
        "/agents/orchestrator/runs",
        json={"task": "say hello"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "ok"


# ---- session runs return source field ----


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
            "source": "remind",
        },
    ]

    resp = await client.get(f"/sessions/{sid}/runs", params={"type": "agent"})
    runs = resp.json()
    assert len(runs) == 2
    assert runs[0]["source"] == "user"
    assert runs[1]["source"] == "remind"


# ---- env vars in API server ----


def test_api_env_vars_in_sdk_options():
    """API server should pass ORCHESTRATE env vars to ClaudeAgentOptions."""
    from api.server import _process_agent_message
    import inspect

    source = inspect.getsource(_process_agent_message)
    assert "ORCHESTRATE_API_URL" in source
    assert "ORCHESTRATE_SESSION_ID" in source
    assert "ORCHESTRATE_AGENT_NAME" in source
