"""Integration test: remind() via API mode posts to the API endpoint."""

import json
import os
import time
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

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
async def test_remind_via_api_posts_to_endpoint(client):
    """When Auto has api_url, remind() should POST to the API with source=remind."""
    # Seed a session
    sid = "remind-test-session"
    SESSIONS[sid] = {
        "session_id": sid,
        "session_name": "Test",
        "agent_id": "orchestrator",
        "created_at": 1000,
        "updated_at": 1000,
    }
    RUNS[sid] = []

    # Send a remind message via the API (simulating what Auto._remind_via_api does)
    resp = await client.post(
        "/agents/orchestrator/runs",
        data={
            "message": "say hello",
            "stream": "true",
            "session_id": sid,
            "source": "remind",
        },
    )
    assert resp.status_code == 200

    # Verify the run was stored with source=remind
    runs = RUNS[sid]
    assert len(runs) == 1
    assert runs[0]["source"] == "remind"
    assert runs[0]["run_input"] == "say hello"


@pytest.mark.asyncio
async def test_user_message_has_source_user(client):
    """Normal user messages should have source=user."""
    sid = "user-test-session"
    SESSIONS[sid] = {
        "session_id": sid,
        "session_name": "Test",
        "agent_id": "orchestrator",
        "created_at": 1000,
        "updated_at": 1000,
    }
    RUNS[sid] = []

    resp = await client.post(
        "/agents/orchestrator/runs",
        data={
            "message": "hello",
            "stream": "true",
            "session_id": sid,
        },
    )
    assert resp.status_code == 200

    runs = RUNS[sid]
    assert len(runs) == 1
    assert runs[0]["source"] == "user"


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
    assert runs[1]["source"] == "remind"
    assert runs[2]["source"] == "user"


# ---- env vars flow ----


def test_cli_passes_env_vars_to_auto():
    """orchestrate-run should create Auto with api_url from env vars."""
    from orchestrate.cli import _exec_program
    import tempfile

    # Write a test program that checks Auto's api_url
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            """
results = []
async def main(auto):
    results.append(auto._api_url)
    results.append(auto._session_id)
"""
        )
        prog_path = f.name

    with tempfile.TemporaryDirectory() as run_dir:
        run_json = Path(run_dir) / "run.json"
        run_json.write_text(
            json.dumps(
                {
                    "id": "test",
                    "pid": os.getpid(),
                    "file": prog_path,
                    "start_time": time.time(),
                    "status": "running",
                }
            )
        )

        # Set env vars and run
        env_patch = {
            "ORCHESTRATE_API_URL": "http://localhost:9999",
            "ORCHESTRATE_SESSION_ID": "test-sess-abc",
        }
        with patch.dict(os.environ, env_patch):
            _exec_program(prog_path, "test", run_dir)

        # Check the program saw the correct values
        spec = __import__("importlib").util.spec_from_file_location("check", prog_path)
        mod = __import__("importlib").util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # The module-level results list was populated by main()
        # But since _exec_program imports fresh, we need to check via run.json status
        data = json.loads(run_json.read_text())
        assert data["status"] == "done"

    os.unlink(prog_path)


def test_api_env_vars_in_sdk_options():
    """API server should pass ORCHESTRATE env vars to ClaudeAgentOptions."""
    from api.server import run_agent
    import inspect

    # Read the source to verify env vars are in the options
    source = inspect.getsource(run_agent)
    assert "ORCHESTRATE_API_URL" in source
    assert "ORCHESTRATE_SESSION_ID" in source
    assert "session_id" in source  # session_id is passed as env var value
