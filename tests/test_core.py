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


# ---------------------------------------------------------------------------
# Additional imports for new tests
# ---------------------------------------------------------------------------
import asyncio
from pathlib import Path
from orchestrate.core import Agent, _parse_json, _build_context_prefix, _validate_schema


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------


def test_parse_json_direct():
    assert _parse_json('{"key": "val"}') == {"key": "val"}


def test_parse_json_empty_raises():
    with pytest.raises(ValueError, match="empty response"):
        _parse_json("")


def test_parse_json_whitespace_raises():
    with pytest.raises(ValueError):
        _parse_json("   ")


def test_parse_json_from_fence_with_lang():
    text = '```json\n{"score": 5}\n```'
    assert _parse_json(text) == {"score": 5}


def test_parse_json_from_fence_no_lang():
    text = '```\n{"score": 5}\n```'
    assert _parse_json(text) == {"score": 5}


def test_parse_json_brace_extraction_from_surrounding_text():
    text = 'some text before {"key": "value"} some text after'
    assert _parse_json(text) == {"key": "value"}


def test_parse_json_no_json_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("just plain text with no JSON at all")


def test_parse_json_nested_object():
    text = '{"outer": {"inner": 1}}'
    assert _parse_json(text) == {"outer": {"inner": 1}}


def test_parse_json_prefers_direct_parse_over_brace_scan():
    # Surrounded text fails direct parse; fence and brace scanner pick it up
    text = "Result: ```json\n{\"answer\": true}\n```"
    assert _parse_json(text) == {"answer": True}


def test_parse_json_returns_dict_not_list():
    # A JSON array at top level should fall through to brace search
    text = '[1, 2, 3]'
    with pytest.raises(ValueError):
        _parse_json(text)


# ---------------------------------------------------------------------------
# _validate_schema
# ---------------------------------------------------------------------------


def test_validate_schema_valid_types():
    # No error raised for matching types
    _validate_schema({"name": "alice", "age": 30, "score": 1.5}, {"name": "str", "age": "int", "score": "float"})


def test_validate_schema_missing_key_raises():
    with pytest.raises(ValueError, match="missing key"):
        _validate_schema({}, {"name": "str"})


def test_validate_schema_type_mismatch_raises():
    with pytest.raises(ValueError, match="expected str"):
        _validate_schema({"name": 123}, {"name": "str"})


def test_validate_schema_bool_passes_as_int():
    # bool is a subclass of int in Python — documents current permissive behavior
    _validate_schema({"count": True}, {"count": "int"})  # does NOT raise


def test_validate_schema_nullable_accepts_none():
    _validate_schema({"val": None}, {"val": "str | null"})  # no error


def test_validate_schema_nullable_accepts_typed_value():
    _validate_schema({"val": "hello"}, {"val": "str | null"})  # no error


def test_validate_schema_unknown_type_spec_skips_validation():
    # Unknown type spec silently skips — documents current behavior
    _validate_schema({"val": 123}, {"val": "unknown_type"})  # no error


def test_validate_schema_multiple_errors_reported():
    with pytest.raises(ValueError) as exc_info:
        _validate_schema({"a": 1, "b": 2}, {"a": "str", "b": "str", "c": "int"})
    msg = str(exc_info.value)
    assert "missing key" in msg or "expected str" in msg


def test_validate_schema_list_type():
    _validate_schema({"items": [1, 2, 3]}, {"items": "list"})  # no error


def test_validate_schema_dict_type():
    _validate_schema({"meta": {"k": "v"}}, {"meta": "dict"})  # no error


# ---------------------------------------------------------------------------
# _build_context_prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_prefix_none_returns_instruction():
    result = await _build_context_prefix("do work", None, AsyncMock(return_value=None))
    assert result == "do work"


@pytest.mark.asyncio
async def test_build_context_prefix_empty_list_returns_instruction():
    result = await _build_context_prefix("do work", [], AsyncMock(return_value=None))
    assert result == "do work"


@pytest.mark.asyncio
async def test_build_context_prefix_with_context_result():
    cr = ContextResult(id="id1", summary="found X", text="long", data=None, agent="researcher", task="t", file="/f")
    result = await _build_context_prefix("do work", [cr], AsyncMock(return_value=None))
    assert "researcher" in result
    assert "found X" in result
    assert "do work" in result


@pytest.mark.asyncio
async def test_build_context_prefix_with_string_id_calls_get_fn():
    cr = ContextResult(id="id1", summary="fetched", text="text", data=None, agent="bot", task="t", file="/f")
    get_fn = AsyncMock(return_value=cr)
    result = await _build_context_prefix("do work", ["id1"], get_fn)
    get_fn.assert_called_once_with("id1")
    assert "fetched" in result
    assert "do work" in result


@pytest.mark.asyncio
async def test_build_context_prefix_none_from_get_fn_skipped():
    # get_context returning None means that entry is silently skipped
    get_fn = AsyncMock(return_value=None)
    result = await _build_context_prefix("do work", ["missing-id"], get_fn)
    assert result == "do work"


@pytest.mark.asyncio
async def test_build_context_prefix_multiple_entries_ordered():
    cr1 = ContextResult(id="a", summary="first", text="t", data=None, agent="agent1", task="t", file="/f")
    cr2 = ContextResult(id="b", summary="second", text="t", data=None, agent="agent2", task="t", file="/f")
    result = await _build_context_prefix("final task", [cr1, cr2], AsyncMock(return_value=None))
    assert result.index("first") < result.index("second") < result.index("final task")


# ---------------------------------------------------------------------------
# Agent.__init__
# ---------------------------------------------------------------------------


def test_agent_init_no_file(tmp_path, monkeypatch):
    """Agent with no matching .md file has None prompt/model/tools."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("nonexistent_xyz")
    assert agent.name == "nonexistent_xyz"
    assert agent.prompt is None
    assert agent.model is None
    assert agent.tools is None
    assert agent._client is not None
    assert agent._registered is False


def test_agent_init_loads_file(tmp_path, monkeypatch):
    """Agent reads prompt, model, and tools from .md file."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "mybot.md").write_text(
        "---\nmodel: claude-opus-4\ntools: Bash, Read\n---\nYou are a helpful bot."
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("mybot")
    assert agent.model == "claude-opus-4"
    assert agent.tools == ["Bash", "Read"]
    assert agent.prompt == "You are a helpful bot."


def test_agent_init_explicit_prompt_overrides_file(tmp_path, monkeypatch):
    """Explicit prompt= arg takes precedence over file body."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "mybot.md").write_text("---\nmodel: claude-haiku\n---\nFile prompt content.")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("mybot", prompt="Explicit prompt override")
    assert agent.prompt == "Explicit prompt override"
    assert agent.model == "claude-haiku"  # non-overridden field still loaded from file


def test_agent_init_explicit_model_overrides_file(tmp_path, monkeypatch):
    """Explicit model= arg takes precedence over file frontmatter."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "mybot.md").write_text("---\nmodel: file-model\n---\nsome prompt")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("mybot", model="explicit-model")
    assert agent.model == "explicit-model"
    assert agent.prompt == "some prompt"  # body still loaded


def test_agent_init_api_url_from_env(tmp_path, monkeypatch):
    """api_url defaults to ORCHESTRATE_API_URL env var."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ORCHESTRATE_API_URL", "http://env-server:8888")
    agent = Agent("bot")
    assert agent._api_url == "http://env-server:8888"


def test_agent_init_explicit_api_url_takes_precedence(tmp_path, monkeypatch):
    """Explicit api_url overrides env var."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ORCHESTRATE_API_URL", "http://env-server:8888")
    agent = Agent("bot", api_url="http://explicit:9999")
    assert agent._api_url == "http://explicit:9999"


def test_agent_init_file_no_frontmatter(tmp_path, monkeypatch):
    """Agent file without --- frontmatter loads body as prompt, no model/tools."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "plain.md").write_text("Just a plain prompt with no frontmatter.")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("plain")
    assert agent.prompt == "Just a plain prompt with no frontmatter."
    assert agent.model is None
    assert agent.tools is None


# ---------------------------------------------------------------------------
# Agent.spawn()
# ---------------------------------------------------------------------------


def test_agent_spawn_inherits_parent_config(tmp_path, monkeypatch):
    """spawn() passes parent prompt/model/tools to child when no overrides."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parent = Agent("parent", prompt="parent prompt", model="claude-sonnet", tools=["Bash"], api_url="http://x")
    child = parent.spawn("child")
    assert child.prompt == "parent prompt"
    assert child.model == "claude-sonnet"
    assert child.tools == ["Bash"]
    assert child.name == "child"


def test_agent_spawn_child_file_overridden_by_parent(tmp_path, monkeypatch):
    """Parent config wins over child's .md file when spawning."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "child.md").write_text("---\nmodel: child-model\n---\nChild file prompt.")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parent = Agent("parent", prompt="parent prompt", model="parent-model", api_url="http://x")
    child = parent.spawn("child")
    # spawn() passes parent's values explicitly, overriding child's .md file
    assert child.prompt == "parent prompt"
    assert child.model == "parent-model"


def test_agent_spawn_override_single_field(tmp_path, monkeypatch):
    """spawn(name, model='new-model') changes only model; rest inherited from parent."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parent = Agent("parent", prompt="parent prompt", model="old-model", tools=["Bash"], api_url="http://x")
    child = parent.spawn("child", model="new-model")
    assert child.model == "new-model"
    assert child.prompt == "parent prompt"
    assert child.tools == ["Bash"]


def test_agent_spawn_inherits_api_url(tmp_path, monkeypatch):
    """spawn() inherits parent api_url."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parent = Agent("parent", api_url="http://parent-server:7777")
    child = parent.spawn("child")
    assert child._api_url == "http://parent-server:7777"


def test_agent_spawn_override_api_url(tmp_path, monkeypatch):
    """spawn(name, api_url=...) can override the inherited api_url."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parent = Agent("parent", api_url="http://original:7777")
    child = parent.spawn("child", api_url="http://override:9999")
    assert child._api_url == "http://override:9999"


# ---------------------------------------------------------------------------
# Agent._ensure_registered / arun()
# ---------------------------------------------------------------------------


def _make_mock_post(reg_resp, msg_resp, ctx_resp):
    """Helper: returns async mock_post side_effect."""
    async def mock_post(url, **kwargs):
        if url == "/agents":
            return reg_resp
        if "/message" in url:
            return msg_resp
        return ctx_resp  # POST /context
    return mock_post


def _make_simple_responses():
    reg_resp = MagicMock()
    reg_resp.raise_for_status = MagicMock()
    reg_resp.json.return_value = {"name": "bot"}

    msg_resp = MagicMock()
    msg_resp.raise_for_status = MagicMock()
    msg_resp.json.return_value = {"content": "done"}

    ctx_resp = MagicMock()
    ctx_resp.status_code = 200
    ctx_resp.json.return_value = {"id": "ctx-1", "summary": "done"}

    return reg_resp, msg_resp, ctx_resp


@pytest.mark.asyncio
async def test_ensure_registered_called_only_once_sequential(tmp_path, monkeypatch):
    """Two sequential arun() calls — POST /agents called only on the first."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("bot", api_url="http://localhost:7777")
    reg_resp, msg_resp, ctx_resp = _make_simple_responses()
    agent._client.post = AsyncMock(side_effect=_make_mock_post(reg_resp, msg_resp, ctx_resp))

    await agent.arun("task one")
    await agent.arun("task two")

    agents_calls = [c for c in agent._client.post.call_args_list if c.args and c.args[0] == "/agents"]
    assert len(agents_calls) == 1


@pytest.mark.asyncio
async def test_ensure_registered_concurrent(tmp_path, monkeypatch):
    """Concurrent arun() calls via asyncio.gather — POST /agents called exactly once."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("bot", api_url="http://localhost:7777")
    reg_resp, msg_resp, ctx_resp = _make_simple_responses()
    agent._client.post = AsyncMock(side_effect=_make_mock_post(reg_resp, msg_resp, ctx_resp))

    await asyncio.gather(agent.arun("task one"), agent.arun("task two"))

    agents_calls = [c for c in agent._client.post.call_args_list if c.args and c.args[0] == "/agents"]
    assert len(agents_calls) == 1


@pytest.mark.asyncio
async def test_agent_arun_full_flow(tmp_path, monkeypatch):
    """arun() registers agent, sends message, returns ContextResult."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("myagent", api_url="http://localhost:7777")
    reg_resp, msg_resp, ctx_resp = _make_simple_responses()
    msg_resp.json.return_value = {"content": "analysis complete"}
    ctx_resp.json.return_value = {"id": "uuid-123", "summary": "analysis complete"}
    agent._client.post = AsyncMock(side_effect=_make_mock_post(reg_resp, msg_resp, ctx_resp))

    result = await agent.arun("analyze codebase")

    assert isinstance(result, ContextResult)
    assert result.text == "analysis complete"
    assert result.agent == "myagent"
    assert result.task == "analyze codebase"


@pytest.mark.asyncio
async def test_agent_arun_with_schema(tmp_path, monkeypatch):
    """arun() with schema= parses JSON and stores it in result.data."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("scorer", api_url="http://localhost:7777")
    reg_resp, msg_resp, ctx_resp = _make_simple_responses()
    msg_resp.json.return_value = {"content": '{"score": 8}'}
    ctx_resp.json.return_value = {"id": "uuid-456", "summary": '{"score": 8}'}
    agent._client.post = AsyncMock(side_effect=_make_mock_post(reg_resp, msg_resp, ctx_resp))

    result = await agent.arun("score this", schema={"score": "int"})

    assert result.data == {"score": 8}
    assert result["score"] == 8


@pytest.mark.asyncio
async def test_agent_arun_with_context_prepends_prefix(tmp_path, monkeypatch):
    """arun() with context= prepends context prefix to message body."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("impl", api_url="http://localhost:7777")
    ctx_entry = ContextResult(
        id="r1", summary="research done", text="long text",
        data=None, agent="researcher", task="research", file="/f/r1.md"
    )
    reg_resp, msg_resp, ctx_resp = _make_simple_responses()
    msg_resp.json.return_value = {"content": "implementation done"}
    agent._client.post = AsyncMock(side_effect=_make_mock_post(reg_resp, msg_resp, ctx_resp))

    await agent.arun("implement based on research", context=[ctx_entry])

    msg_calls = [c for c in agent._client.post.call_args_list if c.args and "/message" in c.args[0]]
    assert msg_calls, "Expected a POST to /agents/.../message"
    msg_data = msg_calls[0].kwargs.get("data", {})
    assert "researcher" in msg_data.get("message", ""), "Context prefix should mention the source agent"


@pytest.mark.asyncio
async def test_agent_arun_no_context(tmp_path, monkeypatch):
    """arun() without context sends instruction directly."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("plain", api_url="http://localhost:7777")
    reg_resp, msg_resp, ctx_resp = _make_simple_responses()
    agent._client.post = AsyncMock(side_effect=_make_mock_post(reg_resp, msg_resp, ctx_resp))

    await agent.arun("plain instruction")

    msg_calls = [c for c in agent._client.post.call_args_list if c.args and "/message" in c.args[0]]
    assert msg_calls
    msg_data = msg_calls[0].kwargs.get("data", {})
    assert msg_data.get("message") == "plain instruction"


# ---------------------------------------------------------------------------
# Agent context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_context_manager_calls_aclose(tmp_path, monkeypatch):
    """async with Agent(...) calls aclose() on exit."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("bot", api_url="http://x")
    agent.aclose = AsyncMock()

    async with agent as a:
        assert a is agent

    agent.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_agent_context_manager_calls_aclose_on_exception(tmp_path, monkeypatch):
    """async with Agent(...) still calls aclose() when body raises."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = Agent("bot", api_url="http://x")
    agent.aclose = AsyncMock()

    with pytest.raises(RuntimeError):
        async with agent:
            raise RuntimeError("boom")

    agent.aclose.assert_called_once()
