import pytest
from unittest.mock import AsyncMock, patch
from orchestrate.core import Orchestrate, Auto, _parse_json, _validate_schema


def test_init_with_api_url():
    orch = Orchestrate(api_url="http://localhost:7777")
    assert orch._api_url == "http://localhost:7777"


def test_auto_is_orchestrate():
    assert Auto is Orchestrate


@pytest.mark.asyncio
async def test_agent_posts_to_api():
    orch = Orchestrate(api_url="http://localhost:7777")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    orch._client.post = AsyncMock(return_value=mock_resp)
    await orch.agent("researcher")
    orch._client.post.assert_called_once_with("/agents", json={"name": "researcher"})
    await orch.aclose()


@pytest.mark.asyncio
async def test_agent_custom_cwd():
    orch = Orchestrate(api_url="http://localhost:7777")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    orch._client.post = AsyncMock(return_value=mock_resp)
    await orch.agent("worker", cwd="/custom")
    orch._client.post.assert_called_once_with(
        "/agents", json={"name": "worker", "cwd": "/custom"}
    )
    await orch.aclose()


@pytest.mark.asyncio
async def test_run_task_posts_ephemeral():
    orch = Orchestrate(api_url="http://localhost:7777")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {"run_id": "abc", "status": "ok"}
    orch._client.post = AsyncMock(return_value=mock_resp)
    result = await orch.run_task("do something", to="worker")
    orch._client.post.assert_called_once_with(
        "/agents/worker/runs", json={"task": "do something"}
    )
    assert result["run_id"] == "abc"
    await orch.aclose()


@pytest.mark.asyncio
async def test_save_context_posts():
    orch = Orchestrate(api_url="http://localhost:7777")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {"id": 1, "created_at": 1000}
    orch._client.post = AsyncMock(return_value=mock_resp)
    result = await orch.save_context("hello", tags=["test"])
    orch._client.post.assert_called_once_with(
        "/context", json={"text": "hello", "tags": ["test"]}
    )
    assert result["id"] == 1
    await orch.aclose()


@pytest.mark.asyncio
async def test_recall_context_gets():
    orch = Orchestrate(api_url="http://localhost:7777")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {"data": [{"text": "hello"}]}
    orch._client.get = AsyncMock(return_value=mock_resp)
    result = await orch.recall_context(q="hello", agent="bot")
    orch._client.get.assert_called_once_with(
        "/context", params={"limit": 50, "q": "hello", "agent": "bot"}
    )
    assert result[0]["text"] == "hello"
    await orch.aclose()


@pytest.mark.asyncio
async def test_remind_delegates_to_run():
    orch = Orchestrate(api_url="http://localhost:7777")
    orch.run = AsyncMock(return_value="done")
    result = await orch.remind("hello")
    orch.run.assert_called_once_with("hello", schema=None)
    assert result == "done"
    await orch.aclose()
