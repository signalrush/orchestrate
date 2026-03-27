# orchestrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a thin Python library that wraps the Claude Agent SDK to coordinate multiple Claude agents through `remind()`/`task()`.

**Architecture:** Single `Auto` class holds a dict of agent sessions. `task()` calls `query()` with session resume. `remind()` delegates to `task(to="self")`. State persistence via atomic JSON file.

**Tech Stack:** Python 3.10+, claude-agent-sdk

---

## File Structure

```
orchestrate/
├── orchestrate/
│   ├── __init__.py      # exports Auto, state
│   ├── core.py          # Auto class + _parse_json helper
│   └── state.py         # persistent key-value store
├── tests/
│   ├── test_core.py     # Auto class unit tests
│   ├── test_state.py    # state module tests
│   └── test_parse.py    # JSON parsing tests
├── pyproject.toml       # package config
└── README.md
```

---

### Task 1: Project scaffold + pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `orchestrate/__init__.py`
- Create: `README.md`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "orchestrate"
version = "0.1.0"
description = "Agent SDK-based orchestration library"
requires-python = ">=3.10"
dependencies = [
    "claude-agent-sdk",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]
```

- [ ] **Step 2: Create orchestrate/__init__.py**

```python
from orchestrate.core import Auto
from orchestrate import state

__all__ = ["Auto", "state"]
```

- [ ] **Step 3: Create README.md**

```markdown
# orchestrate

Thin wrapper over the Claude Agent SDK for coordinating multiple Claude agents.

## Install

```bash
pip install -e .
```

## Usage

```python
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()
    result = await auto.remind("Say hello")
    print(result)

asyncio.run(main())
```
```

- [ ] **Step 4: Commit**

```bash
cd ~/orchestrate
git add pyproject.toml orchestrate/__init__.py README.md
git commit -m "scaffold: pyproject.toml, __init__.py, README"
```

---

### Task 2: JSON parsing helper

**Files:**
- Create: `orchestrate/core.py` (partial — just `_parse_json`)
- Create: `tests/test_parse.py`

- [ ] **Step 1: Write failing tests for _parse_json**

Create `tests/test_parse.py`:

```python
import pytest
from orchestrate.core import _parse_json


def test_parse_plain_json():
    result = _parse_json('{"score": 3.5, "name": "test"}', {"score": "float", "name": "str"})
    assert result == {"score": 3.5, "name": "test"}


def test_parse_json_in_markdown_fence():
    text = 'Here is the result:\n```json\n{"score": 3.5}\n```\nDone.'
    result = _parse_json(text, {"score": "float"})
    assert result == {"score": 3.5}


def test_parse_json_embedded_in_text():
    text = 'The answer is {"score": 3.5} as computed.'
    result = _parse_json(text, {"score": "float"})
    assert result == {"score": 3.5}


def test_parse_no_json_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("no json here", {"score": "float"})


def test_parse_empty_string_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("", {"score": "float"})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/orchestrate
pip install -e ".[dev]" 2>/dev/null
pytest tests/test_parse.py -v
```

Expected: FAIL (module not found or function not defined)

- [ ] **Step 3: Implement _parse_json in core.py**

Create `orchestrate/core.py`:

```python
"""orchestrate core — Auto class and helpers."""

import json
import re
from typing import Any


def _parse_json(text: str, schema: dict) -> dict:
    """Extract JSON object from response text. Lenient parsing.

    Tries in order:
    1. Direct json.loads on stripped text
    2. Extract from markdown ```json ... ``` fences
    3. Find first {...} substring

    Raises ValueError if no valid JSON found.
    """
    text = text.strip()
    if not text:
        raise ValueError("No valid JSON found in empty response")

    # Try 1: direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Try 2: markdown fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Try 3: find first { ... }
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[brace_start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break

    raise ValueError(f"No valid JSON found in response: {text[:200]}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_parse.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add orchestrate/core.py tests/test_parse.py
git commit -m "feat: _parse_json helper with lenient JSON extraction"
```

---

### Task 3: State persistence module

**Files:**
- Create: `orchestrate/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing tests for state module**

Create `tests/test_state.py`:

```python
import json
import os
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Run each test in a temp directory so state files don't collide."""
    monkeypatch.chdir(tmp_path)
    yield
    # Cleanup
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_state.py -v
```

Expected: FAIL (state module not implemented)

- [ ] **Step 3: Implement state.py**

Create `orchestrate/state.py`:

```python
"""Persistent key-value state for orchestrate programs.

Usage:
    from orchestrate import state

    state.set("status", "running")
    state.update({"best": 0.23, "step": 7})
    val = state.get("best")
    all_state = state.get()
"""

import json
import os
import fcntl
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


STATE_FILE = "orchestrate-state.json"


def _get_state_file() -> Path:
    return Path.cwd() / STATE_FILE


def _get_lock_file() -> Path:
    return Path.cwd() / ".orchestrate-state.lock"


def _load_state() -> Dict[str, Any]:
    state_file = _get_state_file()
    if not state_file.exists():
        return {}
    try:
        with open(state_file, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(data: Dict[str, Any]) -> None:
    state_file = _get_state_file()
    temp_fd = None
    temp_path = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            dir=state_file.parent, prefix=".orchestrate-state-", suffix=".tmp"
        )
        with os.fdopen(temp_fd, "w") as temp_file:
            temp_fd = None
            json.dump(data, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.rename(temp_path, state_file)
        temp_path = None
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def _read_modify_write(modifier):
    lock_file = _get_lock_file()
    with open(lock_file, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            current = _load_state()
            modifier(current)
            _save_state(current)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def set(key: str, value: Any) -> None:
    """Set a single key-value pair in state."""
    def _modify(s):
        s[key] = value
    _read_modify_write(_modify)


def update(data: Dict[str, Any]) -> None:
    """Merge a dictionary into current state."""
    def _modify(s):
        s.update(data)
    _read_modify_write(_modify)


def get(key: Optional[str] = None) -> Any:
    """Get a value or entire state dict. Returns None for missing keys."""
    lock_file = _get_lock_file()
    with open(lock_file, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
        try:
            s = _load_state()
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    if key is None:
        return s
    return s.get(key)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_state.py -v
```

Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add orchestrate/state.py tests/test_state.py
git commit -m "feat: state persistence module with atomic writes"
```

---

### Task 4: Auto class — core implementation

**Files:**
- Modify: `orchestrate/core.py` (add Auto class)
- Create: `tests/test_core.py`

- [ ] **Step 1: Write failing tests for Auto class**

Create `tests/test_core.py`:

```python
import pytest
import asyncio
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
    auto.agent("x", cwd="/second")  # should not overwrite
    assert auto._sessions["x"]["cwd"] == "/first"


@pytest.mark.asyncio
async def test_task_auto_creates_agent():
    """task() should auto-create agent if not declared."""
    auto = Auto()

    # Mock query to yield a ResultMessage
    mock_result = MagicMock()
    mock_result.session_id = "sess-123"
    mock_assistant = MagicMock()
    mock_assistant.content = [MagicMock(text="hello")]
    # Make isinstance checks work
    mock_assistant.__class__ = type("AssistantMessage", (), {})
    mock_result.__class__ = type("ResultMessage", (), {})

    async def mock_query(**kwargs):
        yield mock_assistant
        yield mock_result

    with patch("orchestrate.core.query", side_effect=mock_query):
        with patch("orchestrate.core.AssistantMessage", mock_assistant.__class__):
            with patch("orchestrate.core.ResultMessage", mock_result.__class__):
                result = await auto.task("do something", to="new_agent")

    assert "new_agent" in auto._sessions


@pytest.mark.asyncio
async def test_remind_delegates_to_task():
    """remind() should call task(to='self')."""
    auto = Auto()
    auto.task = AsyncMock(return_value="done")
    result = await auto.remind("hello")
    auto.task.assert_called_once_with("hello", to="self", schema=None)
    assert result == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_core.py -v
```

Expected: FAIL (Auto class incomplete)

- [ ] **Step 3: Implement Auto class in core.py**

Add to `orchestrate/core.py` (after `_parse_json`):

```python
import os
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

ALL_TOOLS = [
    "Read", "Edit", "Write", "Bash", "Glob", "Grep",
    "Agent", "WebSearch", "WebFetch", "Skill",
]


class Auto:
    """Orchestrate multiple Claude agents via the Agent SDK.

    Each agent (including 'self') maintains its own session.
    remind() sends to self. task() sends to a named agent.
    """

    def __init__(self, cwd: str | None = None, model: str = "claude-sonnet-4-6"):
        self._sessions: dict[str, dict] = {}
        self._cwd = cwd or os.getcwd()
        self._model = model

    def agent(self, name: str, cwd: str | None = None) -> None:
        """Declare a named agent. Optional — task() auto-creates on first use."""
        if name not in self._sessions:
            self._sessions[name] = {
                "session_id": None,
                "cwd": cwd or self._cwd,
            }

    async def remind(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Send instruction to self. Alias for task(instruction, to='self')."""
        return await self.task(instruction, to="self", schema=schema)

    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Send instruction to a named agent. Accumulates session context."""
        if to not in self._sessions:
            self.agent(to)

        agent = self._sessions[to]
        opts = ClaudeAgentOptions(
            allowed_tools=ALL_TOOLS,
            permission_mode="bypassPermissions",
            cwd=agent["cwd"],
            model=self._model,
            resume=agent["session_id"],
        )

        result_text = ""
        async for msg in query(prompt=instruction, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if hasattr(block, "text"):
                        result_text += block.text
            elif isinstance(msg, ResultMessage):
                agent["session_id"] = msg.session_id

        if schema:
            return _parse_json(result_text, schema)
        return result_text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_core.py -v
```

Expected: 7 PASSED (some may need SDK installed; mock tests should pass regardless)

- [ ] **Step 5: Commit**

```bash
git add orchestrate/core.py tests/test_core.py
git commit -m "feat: Auto class with remind/task/agent"
```

---

### Task 5: Install package and run smoke test

**Files:**
- Create: `examples/hello.py`

- [ ] **Step 1: Install the package**

```bash
cd ~/orchestrate
pip install -e .
```

- [ ] **Step 2: Create a smoke test example**

Create `examples/hello.py`:

```python
"""Smoke test: single remind() call."""
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()
    result = await auto.remind("Say 'orchestrate works' and nothing else.")
    print(f"Result: {result}")
    assert "orchestrate works" in result.lower(), f"Unexpected: {result}"
    print("PASS")

asyncio.run(main())
```

- [ ] **Step 3: Run the smoke test**

```bash
cd ~/orchestrate
python examples/hello.py
```

Expected: prints "Result: orchestrate works" and "PASS"

- [ ] **Step 4: Create a multi-agent smoke test**

Create `examples/multi_agent.py`:

```python
"""Smoke test: two agents + remind, with concurrent gather."""
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()

    # Sequential tasks to two agents
    a = await auto.task("What is 2+2? Reply with just the number.", to="math")
    print(f"Agent 'math' said: {a}")

    b = await auto.task("What is 3+3? Reply with just the number.", to="math")
    print(f"Agent 'math' (turn 2) said: {b}")

    # Self remind
    r = await auto.remind(f"The math agent computed: {a} and {b}. Summarize in one sentence.")
    print(f"Self said: {r}")

    print("PASS")

asyncio.run(main())
```

- [ ] **Step 5: Run multi-agent test**

```bash
python examples/multi_agent.py
```

Expected: three responses printed, "PASS" at end

- [ ] **Step 6: Commit**

```bash
git add examples/
git commit -m "examples: hello and multi-agent smoke tests"
```

---

### Task 6: Schema parsing integration test

**Files:**
- Create: `examples/schema_test.py`

- [ ] **Step 1: Create schema test**

Create `examples/schema_test.py`:

```python
"""Test schema parsing with real SDK calls."""
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()

    r = await auto.remind(
        "What is the capital of France? What is its population in millions?",
        schema={"city": "str", "population_millions": "float"}
    )
    print(f"Result: {r}")
    assert isinstance(r, dict), f"Expected dict, got {type(r)}"
    assert "city" in r, f"Missing 'city' key: {r}"
    assert "population_millions" in r, f"Missing 'population_millions' key: {r}"
    print("PASS")

asyncio.run(main())
```

- [ ] **Step 2: Run schema test**

```bash
cd ~/orchestrate
python examples/schema_test.py
```

Expected: `Result: {'city': 'Paris', 'population_millions': ...}` and "PASS"

- [ ] **Step 3: Commit**

```bash
git add examples/schema_test.py
git commit -m "examples: schema parsing integration test"
```

---

### Task 7: State integration test

**Files:**
- Create: `examples/state_test.py`

- [ ] **Step 1: Create state test**

Create `examples/state_test.py`:

```python
"""Test state persistence."""
import asyncio
import json
from pathlib import Path
from orchestrate import Auto, state

async def main():
    auto = Auto()

    state.set("iteration", 0)
    state.update({"best": 0, "status": "running"})

    assert state.get("iteration") == 0
    assert state.get("best") == 0
    assert state.get("status") == "running"

    state.set("iteration", 5)
    state.update({"best": 42.5})

    assert state.get("iteration") == 5
    assert state.get("best") == 42.5

    # Verify file exists
    data = json.loads(Path("orchestrate-state.json").read_text())
    assert data["iteration"] == 5
    assert data["best"] == 42.5

    # Cleanup
    Path("orchestrate-state.json").unlink(missing_ok=True)
    Path(".orchestrate-state.lock").unlink(missing_ok=True)

    print("PASS")

asyncio.run(main())
```

- [ ] **Step 2: Run state test**

```bash
cd ~/orchestrate
python examples/state_test.py
```

Expected: "PASS"

- [ ] **Step 3: Commit**

```bash
git add examples/state_test.py
git commit -m "examples: state persistence integration test"
```

---

## Self-Review Checklist

- **Spec coverage:** All spec items covered — Auto class (Task 4), remind/task/agent (Task 4), state (Task 3), _parse_json (Task 2), usage examples (Tasks 5-7), pyproject.toml (Task 1).
- **Placeholder scan:** No TBDs, TODOs, or vague steps. All code is complete.
- **Type consistency:** `_parse_json` signature matches usage in `task()`. `Auto` constructor params consistent across all tasks. `state.set/get/update` signatures match tests.
