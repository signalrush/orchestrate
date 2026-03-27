import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orchestrate.core import Auto


def test_init_defaults():
    auto = Auto()
    assert auto._model == "claude-sonnet-4-6"
    assert auto._sessions == {}


def test_init_custom():
    auto = Auto(cwd="/tmp/test", model="claude-opus-4-6")
    assert auto._cwd == "/tmp/test"
    assert auto._model == "claude-opus-4-6"


def test_agent_declares():
    auto = Auto()
    auto.agent("researcher")
    assert "researcher" in auto._sessions
    assert auto._sessions["researcher"]["session_id"] is None


def test_agent_custom_cwd():
    auto = Auto(cwd="/default")
    auto.agent("worker", cwd="/custom")
    assert auto._sessions["worker"]["cwd"] == "/custom"


def test_agent_idempotent():
    auto = Auto()
    auto.agent("x", cwd="/first")
    auto.agent("x", cwd="/second")
    assert auto._sessions["x"]["cwd"] == "/first"


@pytest.mark.asyncio
async def test_remind_delegates_to_task():
    auto = Auto()
    auto.task = AsyncMock(return_value="done")
    result = await auto.remind("hello")
    auto.task.assert_called_once_with("hello", to="self", schema=None)
    assert result == "done"


@pytest.mark.asyncio
async def test_remind_with_schema_delegates():
    auto = Auto()
    auto.task = AsyncMock(return_value={"score": 1.0})
    result = await auto.remind("test", schema={"score": "float"})
    auto.task.assert_called_once_with("test", to="self", schema={"score": "float"})
    assert result == {"score": 1.0}
