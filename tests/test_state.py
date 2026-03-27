import json
import os
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Run each test in a temp directory so state files don't collide."""
    monkeypatch.chdir(tmp_path)
    yield
    for f in tmp_path.glob("orchestrate-state*"):
        f.unlink(missing_ok=True)
    for f in tmp_path.glob(".orchestrate-state*"):
        f.unlink(missing_ok=True)


def test_set_and_get():
    from orchestrate import state
    state.set("score", 42.5)
    assert state.get("score") == 42.5


def test_get_missing_key_returns_none():
    from orchestrate import state
    assert state.get("nonexistent") is None


def test_get_all():
    from orchestrate import state
    state.set("a", 1)
    state.set("b", 2)
    result = state.get()
    assert result == {"a": 1, "b": 2}


def test_update_merges():
    from orchestrate import state
    state.set("a", 1)
    state.update({"b": 2, "c": 3})
    assert state.get() == {"a": 1, "b": 2, "c": 3}


def test_update_overwrites():
    from orchestrate import state
    state.set("a", 1)
    state.update({"a": 99})
    assert state.get("a") == 99


def test_persists_to_file():
    from orchestrate import state
    state.set("x", "hello")
    data = json.loads(Path("orchestrate-state.json").read_text())
    assert data["x"] == "hello"
