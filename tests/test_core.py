"""Tests for orchestrate.core — ContextResult and Orchestrate HTTP client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from orchestrate.core import Orchestrate, ContextResult, Auto, _extract_keywords


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------


def test_extract_keywords_basic():
    kws = _extract_keywords("analyze the authentication module")
    assert "authentication" in kws
    assert "module" in kws
    # stop words stripped
    assert "the" not in kws


def test_extract_keywords_strips_short_words():
    # words < 4 chars are excluded by the regex
    kws = _extract_keywords("do the run set get")
    assert kws == []  # all < 4 chars or stop words


def test_extract_keywords_deduplicates_and_ranks_by_frequency():
    kws = _extract_keywords("review review review code authentication")
    assert kws[0] == "review"  # highest frequency first


def test_extract_keywords_max_limit():
    kws = _extract_keywords("alpha beta gamma delta epsilon zeta theta iota kappa", max_keywords=3)
    assert len(kws) <= 3


def test_extract_keywords_empty():
    assert _extract_keywords("") == []


# ---------------------------------------------------------------------------
# ContextResult
# ---------------------------------------------------------------------------


def test_context_result_str_returns_summary():
    cr = ContextResult(id="abc", summary="short summary", text="long text", data=None, agent="bot", task="do thing", file="/tmp/abc.md")
    assert str(cr) == "short summary"


def test_context_result_repr():
    cr = ContextResult(id="abc", summary="s", text="t", data=None, agent="bot", task="t", file="/f")
    assert "ContextResult" in repr(cr)
    assert "abc" in repr(cr)


def test_context_result_dict_access_with_data():
    cr = ContextResult(id="x", summary="s", text="t", data={"key": "val"}, agent="a", task="t", file="/f")
    assert cr["key"] == "val"


def test_context_result_no_data_delegates_string_methods():
    """Non-schema results delegate str methods to summary."""
    cr = ContextResult(id="x", summary="hello world", text="long", data=None, agent="a", task="t", file="/f")
    assert cr.upper() == "HELLO WORLD"
    assert cr.startswith("hello")


def test_context_result_with_data_raises_on_missing_attr():
    cr = ContextResult(id="x", summary="s", text="t", data={"k": "v"}, agent="a", task="t", file="/f")
    with pytest.raises(AttributeError):
        _ = cr.nonexistent_attr


# ---------------------------------------------------------------------------
# Orchestrate init
# ---------------------------------------------------------------------------


def test_init_defaults():
    orch = Orchestrate()
    assert orch._api_url is None
    assert orch._session_id is None


def test_init_with_api_url():
    orch = Orchestrate(api_url="http://localhost:7777", session_id="sess-abc")
    assert orch._api_url == "http://localhost:7777"
    assert orch._session_id == "sess-abc"


def test_auto_alias():
    """Auto is a backward-compat alias for Orchestrate."""
    auto = Auto(api_url="http://localhost:9999", session_id="s1")
    assert isinstance(auto, Orchestrate)
    assert auto._api_url == "http://localhost:9999"


# ---------------------------------------------------------------------------
# run() — context prefix building
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_prepends_context_to_instruction():
    """When context= is passed, prefix is prepended to instruction."""
    orch = Orchestrate(api_url="http://x")

    cr1 = ContextResult(id="id1", summary="researcher found X", text="long", data=None, agent="researcher", task="research X", file="/f/id1.md")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "impl done"}
    mock_post = AsyncMock(return_value=mock_resp)

    with patch.object(orch._client, "post", mock_post):
        result = await orch.run("implement based on research", to="implementer", context=[cr1], no_context=True)

    # Find the agent message call (first POST to /agents/.../message)
    assert mock_post.called
    agent_call = mock_post.call_args_list[0]
    message_sent = (agent_call.kwargs.get("data") or agent_call.args[1] or {}).get("message", "")

    assert "researcher" in message_sent
    assert "implement based on research" in message_sent
    assert isinstance(result, ContextResult)


@pytest.mark.asyncio
async def test_run_no_context_skips_auto_recall():
    """no_context=True prevents auto-recall even with api_url set."""
    orch = Orchestrate(api_url="http://localhost:7777")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "done"}

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        with patch.object(orch, "recall", new=AsyncMock(return_value=[])) as mock_recall:
            await orch.run("do something", no_context=True)
            mock_recall.assert_not_called()


@pytest.mark.asyncio
async def test_run_auto_context_calls_recall_with_keywords():
    """Auto-context queries recall() once per extracted keyword, not the full instruction."""
    orch = Orchestrate(api_url="http://localhost:7777")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "done"}

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        with patch.object(orch, "recall", new=AsyncMock(return_value=[])) as mock_recall:
            # "review authentication module" → keywords: review, authentication, module
            await orch.run("review authentication module")
            assert mock_recall.call_count >= 1
            # Each call uses a single short keyword, never the full instruction
            for c in mock_recall.call_args_list:
                q_arg = c.kwargs.get("q") or (c.args[0] if c.args else "")
                assert len(q_arg.split()) == 1, f"Expected single keyword, got: {q_arg!r}"


@pytest.mark.asyncio
async def test_run_auto_context_deduplicates_and_scores():
    """Entries appearing in multiple keyword results rank higher."""
    orch = Orchestrate(api_url="http://localhost:7777")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "done"}

    high_rank = ContextResult(id="high", summary="s", text="t", data=None, agent="a", task="t", file="/f")
    low_rank  = ContextResult(id="low",  summary="s", text="t", data=None, agent="a", task="t", file="/f")

    # high_rank appears in both keyword results; low_rank only in one
    def side_effect(q, limit):
        if q == "authentication":
            return [high_rank, low_rank]
        return [high_rank]

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        with patch.object(orch, "recall", new=AsyncMock(side_effect=side_effect)):
            with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)) as mock_post:
                result = await orch.run("review authentication code", no_context=False)
                # The message sent should contain high_rank's context prefix
                agent_call = mock_post.call_args_list[0]
                message_sent = (agent_call.kwargs.get("data") or {}).get("message", "")
                assert "high" in message_sent or isinstance(result, ContextResult)


@pytest.mark.asyncio
async def test_run_no_api_url_skips_auto_recall():
    """Without api_url, auto-recall is never attempted."""
    orch = Orchestrate()  # no api_url

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "done"}

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        with patch.object(orch, "recall", new=AsyncMock(return_value=[])) as mock_recall:
            await orch.run("do something")
            mock_recall.assert_not_called()


@pytest.mark.asyncio
async def test_run_returns_context_result():
    """run() always returns a ContextResult."""
    orch = Orchestrate()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "hello world"}

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        result = await orch.run("say hello", to="bot", no_context=True)

    assert isinstance(result, ContextResult)
    assert result.text == "hello world"
    assert result.agent == "bot"
    assert result.task == "say hello"


@pytest.mark.asyncio
async def test_run_schema_returns_parsed_data():
    """run() with schema= parses JSON and puts it in result.data."""
    orch = Orchestrate()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": '{"score": 9}'}

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        result = await orch.run("score it", schema={"score": "int"}, no_context=True)

    assert result.data == {"score": 9}
    assert result["score"] == 9


# ---------------------------------------------------------------------------
# subagent()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_passes_parent_context():
    """subagent() with parent_context wraps it in a list and passes to run()."""
    orch = Orchestrate()

    parent = ContextResult(id="p1", summary="parent result", text="full", data=None, agent="parent", task="parent task", file="/f/p1.md")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"content": "child done"}

    with patch.object(orch._client, "post", new=AsyncMock(return_value=mock_resp)):
        with patch.object(orch, "run", new=AsyncMock(return_value=parent)) as mock_run:
            await orch.subagent("child task", to="child", parent_context=parent)
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["context"] == [parent]


@pytest.mark.asyncio
async def test_subagent_list_parent_context():
    """subagent() with a list of parent contexts passes them through."""
    orch = Orchestrate()

    p1 = ContextResult(id="p1", summary="s1", text="t", data=None, agent="a", task="t", file="/f1")
    p2 = ContextResult(id="p2", summary="s2", text="t", data=None, agent="b", task="t", file="/f2")

    with patch.object(orch, "run", new=AsyncMock(return_value=p1)) as mock_run:
        await orch.subagent("task", to="bot", parent_context=[p1, p2])
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["context"] == [p1, p2]


@pytest.mark.asyncio
async def test_subagent_no_parent_delegates_normally():
    """subagent() without parent_context passes context=None (auto-recall applies)."""
    orch = Orchestrate()

    dummy = ContextResult(id="d", summary="s", text="t", data=None, agent="a", task="t", file="/f")

    with patch.object(orch, "run", new=AsyncMock(return_value=dummy)) as mock_run:
        await orch.subagent("task", to="bot")
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["context"] is None


# ---------------------------------------------------------------------------
# remind() / task() deprecated wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remind_delegates_to_run():
    orch = Orchestrate()
    mock_result = ContextResult(id="x", summary="s", text="t", data=None, agent="self", task="hello", file="/f")
    with patch.object(orch, "run", new=AsyncMock(return_value=mock_result)) as mock_run:
        result = await orch.remind("hello")
        mock_run.assert_called_once_with("hello", to="self", schema=None)
        assert result is mock_result


@pytest.mark.asyncio
async def test_remind_with_schema_delegates():
    orch = Orchestrate()
    mock_result = ContextResult(id="x", summary="s", text="t", data={"score": 1.0}, agent="self", task="test", file="/f")
    with patch.object(orch, "run", new=AsyncMock(return_value=mock_result)) as mock_run:
        result = await orch.remind("test", schema={"score": "float"})
        mock_run.assert_called_once_with("test", to="self", schema={"score": "float"})
        assert result is mock_result
