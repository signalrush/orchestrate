"""Tests for the REST API server (agent-ui compatibility)."""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from orchestrate.api.server import app, AGENTS, SESSIONS, RUNS, EPHEMERAL_TASKS, _db, AGENT_QUEUES, AGENT_WORKERS


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset in-memory stores between tests."""
    SESSIONS.clear()
    RUNS.clear()
    conn = _db()
    conn.execute("DELETE FROM context_entries")
    conn.commit()
    conn.close()
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
async def test_registered_agent_has_db_id(client):
    """Dynamically registered agents must also include db_id."""
    resp = await client.post("/agents", json={"name": "test-agent"})
    assert resp.status_code == 200
    agent = resp.json()
    assert "db_id" in agent
    assert agent["db_id"]


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
            "source": "system",
        },
    ]
    resp = await client.get(f"/sessions/{sid}/runs", params={"type": "agent"})
    runs = resp.json()
    assert runs[0]["source"] == "user"
    assert runs[1]["source"] == "system"


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


# ---- context endpoints ----

@pytest.mark.asyncio
async def test_save_and_search_context(client):
    """POST /context saves entry, GET /context retrieves it."""
    resp = await client.post("/context", json={"text": "hello world", "tags": ["test"], "agent": "bot"})
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "created_at" in body

    resp = await client.get("/context", params={"q": "hello"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert data[0]["text"] == "hello world"
    assert data[0]["agent"] == "bot"
    assert data[0]["tags"] == ["test"]


@pytest.mark.asyncio
async def test_context_empty_text_rejected(client):
    """POST /context with empty text returns 400."""
    resp = await client.post("/context", json={"text": ""})
    assert resp.status_code == 400

    resp = await client.post("/context", json={"tags": ["foo"]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_context_search_by_tags_exact(client):
    """Tag search uses exact match, not substring."""
    await client.post("/context", json={"text": "entry with alpha", "tags": ["alpha"]})
    await client.post("/context", json={"text": "entry with alphabet", "tags": ["alphabet"]})

    resp = await client.get("/context", params={"tags": "alpha"})
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["text"] == "entry with alpha"


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
async def test_context_pin_nonexistent_returns_404(client):
    resp = await client.post("/context/99999/pin")
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


@pytest.mark.asyncio
async def test_ephemeral_run_happy_path(client):
    """POST /agents/{name}/runs executes task and stores result in DB."""
    from claude_agent_sdk import AssistantMessage, ResultMessage

    # Build mock messages that query() will yield
    text_block = MagicMock()
    text_block.text = '{"answer": 42}'
    del text_block.name  # ensure hasattr(block, "name") is False

    assistant_msg = MagicMock(spec=AssistantMessage)
    assistant_msg.content = [text_block]

    result_msg = MagicMock(spec=ResultMessage)
    result_msg.session_id = "ephemeral-session-abc"

    async def mock_query(prompt, options):
        yield assistant_msg
        yield result_msg

    with patch("orchestrate.api.server.query", side_effect=mock_query):
        with patch("orchestrate.api.server._summarize", return_value="The answer is 42."):
            resp = await client.post(
                "/agents/orchestrator/runs",
                json={"task": "What is the answer?"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "run_id" in body
            assert body["status"] == "ok"
            run_id = body["run_id"]

            # Wait for background task to complete
            if run_id in EPHEMERAL_TASKS:
                await EPHEMERAL_TASKS[run_id]

            # Verify stored in DB
            resp2 = await client.get(f"/runs/{run_id}")
            assert resp2.status_code == 200
            run_data = resp2.json()
            assert run_data["task"] == "What is the answer?"
            assert run_data["text"] == '{"answer": 42}'
            assert run_data["summary"] == "The answer is 42."
            assert run_data["completed_at"] is not None


@pytest.mark.asyncio
async def test_context_unpin_nonexistent_returns_404(client):
    resp = await client.delete("/context/99999/pin")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_context_tags_returned_as_list(client):
    """Tags should be deserialized as JSON array, not raw string."""
    await client.post("/context", json={"text": "tagged", "tags": ["a", "b"]})
    resp = await client.get("/context", params={"q": "tagged"})
    data = resp.json()["data"]
    assert isinstance(data[0]["tags"], list)
    assert data[0]["tags"] == ["a", "b"]


@pytest.mark.asyncio
async def test_context_empty_search(client):
    resp = await client.get("/context")
    assert resp.status_code == 200
    assert "data" in resp.json()


@pytest.mark.asyncio
async def test_kanban_events_on_agent_message(client):
    """post_agent_message emits TaskCreated → TaskStarted → TaskCompleted with matching task_id."""
    import orchestrate.api.server as srv

    emitted: list[dict] = []
    original_emit = srv._emit
    srv._emit = lambda payload: emitted.append(dict(payload))

    # Register an agent via the API
    await client.post("/agents", json={
        "name": "kb-agent",
        "agent_id": "kb-agent",
        "model": {"name": "t", "model": "claude-3-haiku-20240307", "provider": "anthropic"},
    })
    emitted.clear()

    # Mock _process_agent_message to return immediately
    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        return ("task done", None)

    with patch.object(srv, "_process_agent_message", fake_process):
        resp = await client.post(
            "/agents/kb-agent/message",
            data={"message": "analyze the codebase", "source": "system"},
        )

    assert resp.status_code == 200
    event_names = [e["event"] for e in emitted]
    assert "TaskCreated" in event_names
    assert "TaskStarted" in event_names
    assert "TaskCompleted" in event_names

    created = next(e for e in emitted if e["event"] == "TaskCreated")
    completed = next(e for e in emitted if e["event"] == "TaskCompleted")
    assert "task_id" in created
    assert created["task_id"]  # non-empty string
    assert completed["task_id"] == created["task_id"]
    assert "elapsed_secs" in completed
    assert created["title"] == "analyze the codebase"

    # Cleanup
    srv._emit = original_emit
    AGENT_QUEUES.pop("kb-agent", None)
    worker = AGENT_WORKERS.pop("kb-agent", None)
    if worker and not worker.done():
        worker.cancel()


@pytest.mark.asyncio
async def test_user_messages_do_not_create_kanban_events(client):
    """source='user' messages must NOT emit TaskCreated/TaskStarted/TaskCompleted."""
    import orchestrate.api.server as srv

    emitted = []
    original_emit = srv._emit
    srv._emit = lambda payload: emitted.append(dict(payload))

    await client.post("/agents", json={
        "name": "kb-user-agent",
        "agent_id": "kb-user-agent",
    })
    emitted.clear()

    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        return ("done", None)

    with patch.object(srv, "_process_agent_message", fake_process):
        resp = await client.post(
            "/agents/kb-user-agent/message",
            data={"message": "hello from user", "source": "user"},
        )

    assert resp.status_code == 200
    event_names = [e["event"] for e in emitted]
    assert "TaskCreated" not in event_names
    assert "TaskStarted" not in event_names
    assert "TaskCompleted" not in event_names

    srv._emit = original_emit
    from orchestrate.api.server import AGENT_QUEUES, AGENT_WORKERS
    AGENT_QUEUES.pop("kb-user-agent", None)
    worker = AGENT_WORKERS.pop("kb-user-agent", None)
    if worker and not worker.done():
        worker.cancel()


# ---- T1-T10: debugger-identified bug coverage ----


async def _drain_worker(agent_name: str, timeout: float = 2.0):
    """Wait for the agent's worker queue to drain."""
    q = AGENT_QUEUES.get(agent_name)
    if q:
        deadline = asyncio.get_event_loop().time() + timeout
        while not q.empty():
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_load_agent_definitions_unicode_error_skips_file():
    import pathlib
    import tempfile
    from orchestrate.api.server import _load_agent_definitions

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = pathlib.Path(tmpdir)
        (tmppath / "valid.md").write_text("---\nname: myagent\ndescription: test\n---\nDo stuff")
        (tmppath / "bad.md").write_bytes(b"\xff\xfe bad \x00")

        with patch("orchestrate.api.server.Path") as MockPath:
            mock_dir = MagicMock()
            mock_dir.is_dir.return_value = True
            mock_dir.glob.return_value = [tmppath / "valid.md", tmppath / "bad.md"]
            # Path.home() / ".claude" / "agents" — 2 __truediv__ calls
            MockPath.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_dir

            result = _load_agent_definitions()

        assert "myagent" in result
        assert "bad" not in result


@pytest.mark.asyncio
async def test_agent_worker_malformed_item_worker_dies_then_restarts(client):
    # Register agent
    await client.post("/agents", json={"name": "crash-agent"})
    original_worker = AGENT_WORKERS["crash-agent"]

    # Put None into queue — item.get("type") will raise AttributeError
    AGENT_QUEUES["crash-agent"].put_nowait(None)

    # Wait for worker to die
    for _ in range(20):
        await asyncio.sleep(0.05)
        if original_worker.done():
            break

    assert original_worker.done(), "Worker should have died after receiving None"

    # Now patch _process_agent_message and send a real message
    # _ensure_agent_worker will restart the worker
    import orchestrate.api.server as srv

    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        return ("response", None)

    with patch.object(srv, "_process_agent_message", fake_process):
        resp = await client.post(
            "/agents/crash-agent/message",
            data={"message": "hello", "source": "system"},
        )
    assert resp.status_code == 200
    assert resp.json()["content"] is not None

    # Cleanup
    AGENT_QUEUES.pop("crash-agent", None)
    worker = AGENT_WORKERS.pop("crash-agent", None)
    if worker and not worker.done():
        worker.cancel()


@pytest.mark.asyncio
async def test_agent_worker_process_exception_emits_task_failed(client):
    import orchestrate.api.server as srv

    emitted = []
    original_emit = srv._emit
    srv._emit = lambda payload: emitted.append(dict(payload))

    try:
        await client.post("/agents", json={"name": "exc-agent"})
        emitted.clear()

        async def fake_process_raises(msg, source, name, session_id, config, resume_id, run_id):
            raise RuntimeError("sdk crash")

        with patch.object(srv, "_process_agent_message", fake_process_raises):
            # Worker sets future.set_exception → awaiting future raises →
            # FastAPI/Starlette re-raises the exception through ASGI transport
            try:
                resp = await client.post(
                    "/agents/exc-agent/message",
                    data={"message": "do work", "source": "system"},
                )
                resp_status = resp.status_code
            except RuntimeError:
                resp_status = 500

        assert resp_status == 500

        event_names = [e["event"] for e in emitted]
        assert "TaskFailed" in event_names

        created = next((e for e in emitted if e["event"] == "TaskCreated"), None)
        failed = next((e for e in emitted if e["event"] == "TaskFailed"), None)
        assert created is not None
        assert failed is not None
        assert failed["task_id"] == created["task_id"]

        # Verify worker is still alive and can process another message
        async def fake_process_ok(msg, source, name, session_id, config, resume_id, run_id):
            return ("ok", None)

        with patch.object(srv, "_process_agent_message", fake_process_ok):
            resp2 = await client.post(
                "/agents/exc-agent/message",
                data={"message": "another task", "source": "system"},
            )
        assert resp2.status_code == 200
    finally:
        srv._emit = original_emit
        AGENT_QUEUES.pop("exc-agent", None)
        worker = AGENT_WORKERS.pop("exc-agent", None)
        if worker and not worker.done():
            worker.cancel()


@pytest.mark.xfail(strict=True, reason="B3: worker uses stale config")
@pytest.mark.asyncio
async def test_reregister_agent_worker_uses_updated_config(client):
    # Documents bug B3: worker's INSERT OR REPLACE overwrites DB with old config
    import orchestrate.api.server as srv

    captured_models = []

    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        captured_models.append(config.get("model"))
        return ("done", None)

    with patch.object(srv, "_process_agent_message", fake_process):
        # Register with haiku
        await client.post("/agents", json={
            "name": "model-agent",
            "model": {"name": "t", "model": "claude-haiku-4-5-20251001", "provider": "anthropic"},
        })
        resp1 = await client.post(
            "/agents/model-agent/message",
            data={"message": "hello", "source": "user"},
        )
        assert resp1.status_code == 200

        # Re-register with opus
        await client.post("/agents", json={
            "name": "model-agent",
            "model": {"name": "t", "model": "claude-opus-4-6", "provider": "anthropic"},
        })
        resp2 = await client.post(
            "/agents/model-agent/message",
            data={"message": "hello again", "source": "user"},
        )
        assert resp2.status_code == 200

    assert len(captured_models) >= 2
    # Currently FAILS (B3): worker uses old config from when worker started
    # This documents the bug — test shows what SHOULD happen
    assert captured_models[-1] == {"name": "t", "model": "claude-opus-4-6", "provider": "anthropic"}

    AGENT_QUEUES.pop("model-agent", None)
    worker = AGENT_WORKERS.pop("model-agent", None)
    if worker and not worker.done():
        worker.cancel()


@pytest.mark.xfail(strict=True, reason="B6: DELETE /sessions does not remove DB runs")
@pytest.mark.asyncio
async def test_delete_session_also_removes_from_db(client):
    # Documents bug B6: DELETE /sessions only removes from in-memory, NOT from DB
    SESSIONS["sess-to-delete"] = {"session_id": "sess-to-delete"}
    conn = _db()
    conn.execute(
        "INSERT INTO runs (agent_name, session_id, source, input, content, tools, created_at) VALUES (?,?,?,?,?,?,?)",
        ("orchestrator", "sess-to-delete", "user", "hi", "hello", "[]", 1),
    )
    conn.commit()
    conn.close()

    resp = await client.delete("/sessions/sess-to-delete", params={"db_id": ""})
    assert resp.status_code == 200
    assert "sess-to-delete" not in SESSIONS

    conn = _db()
    rows = conn.execute("SELECT * FROM runs WHERE session_id = ?", ("sess-to-delete",)).fetchall()
    conn.close()
    # Currently FAILS (B6): runs are NOT deleted from DB
    assert len(rows) == 0, "DB runs should be deleted when session is deleted"


@pytest.mark.xfail(strict=True, reason="B7: pinned entries bypass query filter")
@pytest.mark.asyncio
async def test_search_context_pinned_entry_does_not_bypass_query_filter(client):
    # Documents bug B7: pinned entries bypass query filter
    await client.post("/context", json={"text": "completely unrelated content", "tags": []})
    resp_unrelated = await client.get("/context", params={"q": "completely unrelated"})
    unrelated_id = resp_unrelated.json()["data"][0]["id"]
    await client.post(f"/context/{unrelated_id}/pin")

    await client.post("/context", json={"text": "target content we want", "tags": []})

    resp = await client.get("/context", params={"q": "target"})
    data = resp.json()["data"]

    # Currently FAILS (B7): pinned entry also shows up even though it doesn't match "target"
    assert len(data) == 1, f"Expected 1 result, got {len(data)}: {[d['text'] for d in data]}"
    assert data[0]["text"] == "target content we want"


@pytest.mark.asyncio
async def test_register_agent_with_model_as_dict_does_not_cause_type_error(client):
    import orchestrate.api.server as srv

    resp = await client.post("/agents", json={
        "name": "dict-model-agent",
        "model": {"name": "t", "model": "claude-haiku-4-5-20251001", "provider": "anthropic"},
    })
    assert resp.status_code == 200
    agent = resp.json()
    assert agent["model"] == {"name": "t", "model": "claude-haiku-4-5-20251001", "provider": "anthropic"}

    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        model = config.get("model", "claude-sonnet-4-6")
        return (f"model type: {type(model).__name__}", None)

    with patch.object(srv, "_process_agent_message", fake_process):
        resp2 = await client.post(
            "/agents/dict-model-agent/message",
            data={"message": "test", "source": "user"},
        )
    assert resp2.status_code == 200

    AGENT_QUEUES.pop("dict-model-agent", None)
    worker = AGENT_WORKERS.pop("dict-model-agent", None)
    if worker and not worker.done():
        worker.cancel()


@pytest.mark.asyncio
async def test_ephemeral_run_schema_without_properties_key_does_not_crash(client):
    from claude_agent_sdk import AssistantMessage, ResultMessage

    text_block = MagicMock()
    text_block.text = '"hello"'
    del text_block.name

    assistant_msg = MagicMock(spec=AssistantMessage)
    assistant_msg.content = [text_block]

    result_msg = MagicMock(spec=ResultMessage)
    result_msg.session_id = "ephemeral-no-props"

    async def mock_query(prompt, options):
        yield assistant_msg
        yield result_msg

    with patch("orchestrate.api.server.query", side_effect=mock_query):
        with patch("orchestrate.api.server._summarize", return_value="a string result"):
            resp = await client.post(
                "/agents/orchestrator/runs",
                json={"task": "return a string", "schema": {"type": "string"}},
            )
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]

            if run_id in EPHEMERAL_TASKS:
                await EPHEMERAL_TASKS[run_id]

            for _ in range(20):
                run_resp = await client.get(f"/runs/{run_id}")
                if run_resp.status_code == 200 and run_resp.json().get("completed_at"):
                    break
                await asyncio.sleep(0.1)

            # Currently may FAIL (B10: KeyError on schema["properties"])
            run_data = run_resp.json()
            assert run_data["completed_at"] is not None, "Run should complete without crashing"
            assert "KeyError" not in (run_data.get("summary") or ""), "Should not have KeyError in summary"


@pytest.mark.xfail(
    reason="B: DELETE /agents/{name} always returns 200 even for nonexistent agents",
    strict=False,
)
@pytest.mark.asyncio
async def test_delete_nonexistent_agent_returns_404(client):
    resp = await client.delete("/agents/agent-that-does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_agent_message_self_alias_and_title_field(client):
    import orchestrate.api.server as srv

    emitted = []
    original_emit = srv._emit
    srv._emit = lambda payload: emitted.append(dict(payload))

    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        return ("done", None)

    try:
        with patch.object(srv, "_process_agent_message", fake_process):
            resp = await client.post(
                "/agents/self/message",
                data={
                    "message": "do actual work description",
                    "source": "system",
                    "title": "Custom Task Title",
                },
            )
        assert resp.status_code == 200

        created_events = [e for e in emitted if e["event"] == "TaskCreated"]
        assert len(created_events) == 1
        created = created_events[0]

        # "self" should be aliased to "orchestrator"
        assert created["agent_name"] == "orchestrator"
        # title field should override message[:80] as the task title
        assert created["title"] == "Custom Task Title"
    finally:
        srv._emit = original_emit
        AGENT_QUEUES.pop("orchestrator", None)
        worker = AGENT_WORKERS.pop("orchestrator", None)
        if worker and not worker.done():
            worker.cancel()


# ---- additional coverage ----


@pytest.mark.asyncio
async def test_register_agent_duplicate_name_overwrites(client):
    await client.post("/agents", json={
        "name": "dup-agent",
        "model": {"name": "t", "model": "claude-haiku-4-5-20251001", "provider": "anthropic"},
    })
    resp2 = await client.post("/agents", json={
        "name": "dup-agent",
        "model": {"name": "t", "model": "claude-opus-4-6", "provider": "anthropic"},
    })
    assert resp2.status_code == 200
    assert AGENTS["dup-agent"]["model"]["model"] == "claude-opus-4-6"

    AGENT_QUEUES.pop("dup-agent", None)
    worker = AGENT_WORKERS.pop("dup-agent", None)
    if worker and not worker.done():
        worker.cancel()


@pytest.mark.asyncio
async def test_register_agent_missing_name_uses_uuid(client):
    import re

    resp = await client.post("/agents", json={})
    assert resp.status_code == 200
    agent = resp.json()
    assert "id" in agent
    assert re.match(r"^[0-9a-f-]{36}$", agent["id"])

    # Cleanup the auto-named agent
    agent_id = agent["id"]
    AGENT_QUEUES.pop(agent_id, None)
    worker = AGENT_WORKERS.pop(agent_id, None)
    if worker and not worker.done():
        worker.cancel()


@pytest.mark.asyncio
async def test_delete_agent_cleanup(client):
    await client.post("/agents", json={"name": "cleanup-agent"})
    assert "cleanup-agent" in AGENTS

    resp = await client.delete("/agents/cleanup-agent")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert "cleanup-agent" not in AGENTS
    assert "cleanup-agent" not in AGENT_QUEUES

    conn = _db()
    row = conn.execute("SELECT * FROM agents WHERE name = ?", ("cleanup-agent",)).fetchone()
    conn.close()
    assert row is None


@pytest.mark.asyncio
async def test_post_agent_message_unknown_agent_returns_404(client):
    resp = await client.post("/agents/nobody/message", data={"message": "hi", "source": "user"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_context_by_id(client):
    resp = await client.post("/context", json={"text": "specific entry", "tags": ["x"]})
    entry_id = resp.json()["id"]
    resp2 = await client.get(f"/context/{entry_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["text"] == "specific entry"
    assert data["tags"] == ["x"]


@pytest.mark.asyncio
async def test_delete_context_entry(client):
    resp = await client.post("/context", json={"text": "to delete"})
    entry_id = resp.json()["id"]
    resp2 = await client.delete(f"/context/{entry_id}")
    assert resp2.status_code == 200
    assert resp2.json() == {"status": "deleted"}
    resp3 = await client.get(f"/context/{entry_id}")
    assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_delete_context_nonexistent_returns_404(client):
    resp = await client.delete("/context/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sse_emit_reaches_subscriber():
    from orchestrate.api.server import TEAM_SSE_SUBSCRIBERS, _emit

    q = asyncio.Queue()
    TEAM_SSE_SUBSCRIBERS.append(q)
    try:
        _emit({"event": "TestEvent", "data": "hello"})
        assert not q.empty()
        item = q.get_nowait()
        data = json.loads(item)
        assert data["event"] == "TestEvent"
    finally:
        TEAM_SSE_SUBSCRIBERS.remove(q)


@pytest.mark.asyncio
async def test_load_agent_definitions_no_agents_dir():
    from orchestrate.api.server import _load_agent_definitions

    with patch("orchestrate.api.server.Path") as MockPath:
        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = False
        # Path.home() / ".claude" / "agents" — 2 __truediv__ calls
        MockPath.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_dir
        result = _load_agent_definitions()
    assert result == {}


@pytest.mark.asyncio
async def test_load_agent_definitions_no_frontmatter_skipped():
    import pathlib
    import tempfile
    from orchestrate.api.server import _load_agent_definitions

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = pathlib.Path(tmpdir)
        (tmppath / "nofrontmatter.md").write_text("This has no YAML frontmatter")

        with patch("orchestrate.api.server.Path") as MockPath:
            mock_dir = MagicMock()
            mock_dir.is_dir.return_value = True
            mock_dir.glob.return_value = [tmppath / "nofrontmatter.md"]
            # Path.home() / ".claude" / "agents" — 2 __truediv__ calls
            MockPath.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_dir
            result = _load_agent_definitions()
        assert result == {}
