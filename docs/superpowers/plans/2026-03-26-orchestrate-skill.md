# Orchestrate Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Claude Code skill and CLI wrapper so the model can write Python programs that drive agent execution via the orchestrate library.

**Architecture:** Move source to `src/orchestrate/`, add `cli.py` for background process management with multi-run support, add `skills/orchestrate/SKILL.md` as the skill prompt. Programs run directly via Agent SDK — no stop hooks.

**Tech Stack:** Python 3.10+, claude-agent-sdk, setuptools, asyncio

---

## File Structure

```
orchestrate/
├── src/
│   └── orchestrate/
│       ├── __init__.py      # (MOVE) exports Auto, state
│       ├── core.py          # (MOVE) Auto class
│       ├── state.py         # (MOVE) persistent state
│       └── cli.py           # (NEW) orchestrate-run CLI
├── skills/
│   └── orchestrate/
│       └── SKILL.md         # (NEW) skill definition
├── api/
│   └── server.py            # (UPDATE) import path
├── tests/
│   ├── test_core.py         # (UPDATE) import path
│   ├── test_parse.py        # (UPDATE) import path
│   ├── test_state.py        # (UPDATE) import path
│   ├── test_api.py          # (UPDATE) import path
│   └── test_cli.py          # (NEW) CLI tests
├── pyproject.toml            # (UPDATE) src layout + entry point
└── ...
```

---

### Task 1: Restructure source to `src/` layout

**Files:**
- Move: `orchestrate/*.py` → `src/orchestrate/*.py`
- Modify: `pyproject.toml`
- Modify: `api/server.py:17` (import path)
- Modify: `tests/test_api.py:6` (import path, remove sys.path hack)

- [ ] **Step 1: Create `src/orchestrate/` and move files**

```bash
mkdir -p src/orchestrate
mv orchestrate/__init__.py src/orchestrate/__init__.py
mv orchestrate/core.py src/orchestrate/core.py
mv orchestrate/state.py src/orchestrate/state.py
rm -rf orchestrate/  # remove old package dir (includes __pycache__)
```

- [ ] **Step 2: Update `pyproject.toml`**

Replace the entire file with:

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

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
api = ["fastapi", "uvicorn[standard]", "python-multipart"]

[project.scripts]
orchestrate-run = "orchestrate.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "strict"
```

- [ ] **Step 3: Remove the sys.path hack from `tests/test_api.py`**

Remove these lines from the top of `tests/test_api.py`:

```python
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
```

The `src/` layout with editable install handles imports.

- [ ] **Step 4: Reinstall package and run all tests**

```bash
pip install -e ".[dev,api]"
pytest tests/ -v
```

Expected: All 27 tests pass (18 unit + 9 API).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: move source to src/ layout"
```

---

### Task 2: Write the CLI — run command

**Files:**
- Create: `src/orchestrate/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test for the `run` command**

Create `tests/test_cli.py`:

```python
"""Tests for the orchestrate-run CLI."""

import json
import os
import signal
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrate.cli import cmd_run, cmd_list, cmd_status, cmd_stop, RUNS_DIR


@pytest.fixture(autouse=True)
def clean_runs_dir(tmp_path):
    """Use a temp directory for runs."""
    test_runs = tmp_path / "runs"
    test_runs.mkdir()
    with patch("orchestrate.cli.RUNS_DIR", test_runs):
        yield test_runs


def _write_test_program(path: Path, body: str = "pass") -> Path:
    """Write a minimal async program file."""
    prog = path / "test_prog.py"
    prog.write_text(f"async def main(auto):\n    {body}\n")
    return prog


def test_run_creates_run_dir_and_json(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path, body="pass")
    run_id = cmd_run(str(prog))

    assert len(run_id) == 4
    run_dir = clean_runs_dir / run_id
    assert run_dir.exists()

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json["id"] == run_id
    assert run_json["file"] == str(prog)
    assert run_json["status"] in ("running", "done")
    assert "pid" in run_json
    assert "start_time" in run_json


def test_run_creates_output_log(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path)
    run_id = cmd_run(str(prog))
    time.sleep(1)  # let background process start

    log_file = clean_runs_dir / run_id / "output.log"
    assert log_file.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrate.cli'`

- [ ] **Step 3: Implement `cli.py` — run command + helpers**

Create `src/orchestrate/cli.py`:

```python
"""orchestrate-run CLI — manage background orchestrate programs."""

import argparse
import asyncio
import importlib.util
import inspect
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from orchestrate.core import Auto


RUNS_DIR = Path.home() / ".orchestrate" / "runs"


def _short_id() -> str:
    """Generate a 4-char run ID."""
    return uuid.uuid4().hex[:4]


def _ensure_runs_dir():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def _read_run_json(run_id: str) -> dict:
    path = _run_dir(run_id) / "run.json"
    return json.loads(path.read_text())


def _write_run_json(run_id: str, data: dict):
    path = _run_dir(run_id) / "run.json"
    path.write_text(json.dumps(data, indent=2))


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cmd_run(file_path: str) -> str:
    """Launch a program as a background process. Returns run ID."""
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        print(f"Error: {file_path} not found", file=sys.stderr)
        sys.exit(1)

    _ensure_runs_dir()
    run_id = _short_id()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True)

    log_path = run_dir / "output.log"

    # Launch background process
    worker = os.path.abspath(__file__)
    proc = subprocess.Popen(
        [sys.executable, worker, "_exec", file_path, run_id],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    _write_run_json(run_id, {
        "id": run_id,
        "pid": proc.pid,
        "file": file_path,
        "start_time": time.time(),
        "status": "running",
    })

    print(f"Started run {run_id} (PID {proc.pid})")
    print(f"Log: {log_path}")
    return run_id


def _exec_program(file_path: str, run_id: str):
    """Execute a user program (called in background subprocess)."""
    try:
        spec = importlib.util.spec_from_file_location("user_program", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        main_fn = getattr(module, "main", None)
        if main_fn is None:
            raise RuntimeError(f"No main() function found in {file_path}")

        if not inspect.iscoroutinefunction(main_fn):
            raise RuntimeError("main() must be async (async def main)")

        auto = Auto()
        asyncio.run(main_fn(auto))

        _write_run_json(run_id, {
            **_read_run_json(run_id),
            "status": "done",
        })
    except Exception as e:
        try:
            _write_run_json(run_id, {
                **_read_run_json(run_id),
                "status": "error",
                "error": str(e),
            })
        except Exception:
            pass
        print(f"Error: {e}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(prog="orchestrate-run")
    sub = parser.add_subparsers(dest="command")

    # Default: if first arg is a .py file, treat as run
    run_p = sub.add_parser("run", help="Run a program")
    run_p.add_argument("file", help="Python file to run")

    list_p = sub.add_parser("list", help="List all runs")

    status_p = sub.add_parser("status", help="Show run status")
    status_p.add_argument("id", nargs="?", help="Run ID")

    stop_p = sub.add_parser("stop", help="Stop a run")
    stop_p.add_argument("id", nargs="?", help="Run ID")
    stop_p.add_argument("--all", action="store_true", help="Stop all runs")

    log_p = sub.add_parser("log", help="Tail run output")
    log_p.add_argument("id", help="Run ID")

    # Also handle: orchestrate-run file.py (no subcommand)
    args, remaining = parser.parse_known_args()

    if args.command == "_exec":
        # Internal: called by background subprocess
        _exec_program(remaining[0], remaining[1])
    elif args.command == "run":
        cmd_run(args.file)
    elif args.command == "list":
        cmd_list()
    elif args.command == "status":
        cmd_status(args.id)
    elif args.command == "stop":
        cmd_stop(args.id, all_runs=args.all)
    elif args.command == "log":
        cmd_log(args.id)
    elif args.command is None and remaining and remaining[0].endswith(".py"):
        # orchestrate-run file.py (shorthand)
        cmd_run(remaining[0])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cli.py::test_run_creates_run_dir_and_json tests/test_cli.py::test_run_creates_output_log -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrate/cli.py tests/test_cli.py
git commit -m "feat: add orchestrate-run CLI with run command"
```

---

### Task 3: Write the CLI — list, status, stop, log commands

**Files:**
- Modify: `src/orchestrate/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for list, status, stop**

Append to `tests/test_cli.py`:

```python
def test_list_shows_runs(clean_runs_dir):
    """Seed two run dirs and verify list returns them."""
    for rid, status in [("ab12", "running"), ("cd34", "done")]:
        d = clean_runs_dir / rid
        d.mkdir()
        (d / "run.json").write_text(json.dumps({
            "id": rid, "pid": 99999, "file": f"{rid}.py",
            "start_time": time.time(), "status": status,
        }))
        (d / "output.log").write_text("")

    runs = cmd_list()
    assert len(runs) == 2
    ids = {r["id"] for r in runs}
    assert ids == {"ab12", "cd34"}


def test_status_returns_run_info(clean_runs_dir):
    rid = "ef56"
    d = clean_runs_dir / rid
    d.mkdir()
    (d / "run.json").write_text(json.dumps({
        "id": rid, "pid": os.getpid(), "file": "test.py",
        "start_time": time.time(), "status": "running",
    }))
    (d / "output.log").write_text("line1\nline2\n")

    info = cmd_status(rid)
    assert info["id"] == rid
    assert info["alive"] is True


def test_stop_kills_process(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path, body="import time; time.sleep(60)")
    run_id = cmd_run(str(prog))
    time.sleep(1)

    run_json = _read_run_json_from(clean_runs_dir / run_id)
    pid = run_json["pid"]
    assert _is_alive(pid)

    cmd_stop(run_id)
    time.sleep(1)

    assert not _is_alive(pid)


def _read_run_json_from(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cli.py -v
```

Expected: FAIL — `cmd_list`, `cmd_status`, `cmd_stop` not yet returning proper values.

- [ ] **Step 3: Implement list, status, stop, log in `cli.py`**

Add these functions to `src/orchestrate/cli.py`:

```python
def _time_ago(ts: float) -> str:
    """Human-readable time since timestamp."""
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta}s ago"
    elif delta < 3600:
        return f"{delta // 60} min ago"
    else:
        return f"{delta // 3600}h ago"


def cmd_list() -> list[dict]:
    """List all runs. Returns list of run info dicts."""
    _ensure_runs_dir()
    runs = []
    for entry in sorted(RUNS_DIR.iterdir()):
        rj = entry / "run.json"
        if not rj.exists():
            continue
        data = json.loads(rj.read_text())
        # Update status if process died
        if data["status"] == "running" and not _is_alive(data["pid"]):
            data["status"] = "dead"
        runs.append(data)

    # Print table
    if not runs:
        print("No runs found.")
    else:
        print(f"{'ID':<10}{'FILE':<30}{'STATUS':<10}{'STARTED'}")
        for r in runs:
            fname = os.path.basename(r["file"])
            started = _time_ago(r["start_time"])
            print(f"{r['id']:<10}{fname:<30}{r['status']:<10}{started}")

    return runs


def cmd_status(run_id: str | None = None) -> dict | None:
    """Show status for a specific run, or list all if no ID given."""
    if not run_id:
        cmd_list()
        return None

    _ensure_runs_dir()
    rd = _run_dir(run_id)
    if not rd.exists():
        print(f"Run {run_id} not found.", file=sys.stderr)
        return None

    data = _read_run_json(run_id)
    alive = data["status"] == "running" and _is_alive(data["pid"])

    print(f"Run:     {data['id']}")
    print(f"File:    {data['file']}")
    print(f"PID:     {data['pid']}")
    print(f"Status:  {data['status']}")
    print(f"Alive:   {alive}")
    print(f"Started: {_time_ago(data['start_time'])}")

    if data.get("error"):
        print(f"Error:   {data['error']}")

    log_path = rd / "output.log"
    if log_path.exists():
        lines = log_path.read_text().splitlines()
        tail = lines[-20:] if len(lines) > 20 else lines
        if tail:
            print(f"\n--- Last {len(tail)} lines ---")
            for line in tail:
                print(line)

    return {**data, "alive": alive}


def cmd_stop(run_id: str | None = None, all_runs: bool = False):
    """Stop a run by ID, or all runs with --all."""
    _ensure_runs_dir()

    if all_runs:
        for entry in RUNS_DIR.iterdir():
            rj = entry / "run.json"
            if not rj.exists():
                continue
            data = json.loads(rj.read_text())
            if data["status"] == "running" and _is_alive(data["pid"]):
                _kill(data, entry)
        return

    if not run_id:
        print("Usage: orchestrate-run stop <id> or --all", file=sys.stderr)
        return

    rd = _run_dir(run_id)
    if not rd.exists():
        print(f"Run {run_id} not found.", file=sys.stderr)
        return

    data = _read_run_json(run_id)
    _kill(data, rd)


def _kill(data: dict, rd: Path):
    """Send SIGTERM, wait, SIGKILL if needed."""
    pid = data["pid"]
    if not _is_alive(pid):
        print(f"Run {data['id']} (PID {pid}) already dead.")
        return

    print(f"Stopping run {data['id']} (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(30):  # wait up to 3s
        time.sleep(0.1)
        if not _is_alive(pid):
            break
    else:
        os.kill(pid, signal.SIGKILL)

    data["status"] = "stopped"
    (rd / "run.json").write_text(json.dumps(data, indent=2))
    print(f"Stopped.")


def cmd_log(run_id: str):
    """Tail the output log for a run."""
    _ensure_runs_dir()
    rd = _run_dir(run_id)
    if not rd.exists():
        print(f"Run {run_id} not found.", file=sys.stderr)
        return

    log_path = rd / "output.log"
    if not log_path.exists():
        print("No log file yet.")
        return

    # Use tail -f for live streaming
    os.execvp("tail", ["tail", "-f", str(log_path)])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cli.py -v
```

Expected: All CLI tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrate/cli.py tests/test_cli.py
git commit -m "feat: add list, status, stop, log CLI commands"
```

---

### Task 4: Write the SKILL.md

**Files:**
- Create: `skills/orchestrate/SKILL.md`

- [ ] **Step 1: Create the skill directory**

```bash
mkdir -p skills/orchestrate
```

- [ ] **Step 2: Write `skills/orchestrate/SKILL.md`**

```markdown
---
name: orchestrate
description: Run yourself in a loop with programmatic control via the Agent SDK. Use for long-running tasks like optimization, research, iterative improvement, multi-agent coordination, or any multi-step workflow where you need to repeat, branch, or track progress. Triggers on "orchestrate", "run a loop", "multi-agent", "keep improving", "optimize", "iterate", or when a Python file with def main(auto) exists.
---

# Orchestrate — Programmatic agent control via the Agent SDK

A Python program drives agent execution. Each `auto.remind()` sends an instruction to an agent via the Claude Agent SDK. Use `auto.task()` to dispatch work to other agents. The program controls the loop, branching, and state.

## How to launch

1. Write a Python file with `async def main(auto):`
2. Run it:

```bash
orchestrate-run <file.py>
```

## Writing a program

```python
async def main(auto):
    baseline = await auto.remind(
        "Run train.py and report val_loss",
        schema={"val_loss": "float"}
    )
    best = baseline["val_loss"]

    for i in range(20):
        result = await auto.remind(
            f"Experiment {i+1}: try to beat val_loss={best}. "
            "Edit train.py, commit, run, report.",
            schema={"val_loss": "float", "description": "str"}
        )

        if result["val_loss"] < best:
            best = result["val_loss"]
            await auto.remind(f"Good, improved to {best}. Keep it.")
        else:
            await auto.remind("Didn't improve. Revert: git reset --hard HEAD~1")

        if (i + 1) % 5 == 0:
            await auto.remind("Reflect: what's working? What to try next?")
```

No imports needed — the `auto` object is passed to `main`.

## API

```python
result = await auto.remind(instruction)              # returns str
result = await auto.remind(instruction, schema={})   # returns dict
result = await auto.task(instruction, to="agent")    # dispatch to another agent
auto.agent(name, cwd=None)                           # declare an agent
```

### `auto.remind(instruction, schema=None)`
Send instruction to an agent. Returns the response as a string, or parsed dict if schema provided.

### `auto.task(instruction, to, schema=None)`
Assign work to a named agent. Each agent accumulates its own session context.

### `auto.agent(name, cwd=None)`
Declare an agent before first use. Optional — `task(to="name")` auto-creates agents.

## Manage runs

Multiple programs can run concurrently. Each gets a short ID.

```bash
orchestrate-run list              # show all runs
orchestrate-run status <id>       # details + recent log
orchestrate-run log <id>          # tail live output
orchestrate-run stop <id>         # stop one run
orchestrate-run stop --all        # stop all runs
```

## State tracking (optional)

```python
from orchestrate import state

async def main(auto):
    state.set("status", "running")
    for i in range(100):
        result = await auto.remind(f"experiment {i}", schema={"score": "float"})
        state.update({"step": i, "score": result["score"]})
    state.set("status", "done")
```

Progress visible via `cat orchestrate-state.json`.

## Patterns

### Optimization loop
```python
async def main(auto):
    best = 999
    for i in range(20):
        r = await auto.remind(f"Try to beat {best}", schema={"loss": "float"})
        if r["loss"] < best:
            best = r["loss"]
        else:
            await auto.remind("Revert")
```

### Multi-agent
```python
async def main(auto):
    auto.agent("researcher", cwd="/home/user/research")
    auto.agent("coder", cwd="/home/user/project")

    findings = await auto.task("Survey recent papers on X", to="researcher")
    await auto.task(f"Implement based on: {findings}", to="coder")
```

### Error recovery
```python
async def main(auto):
    for i in range(20):
        try:
            r = await auto.remind(f"Experiment {i}", schema={"loss": "float"})
        except Exception as e:
            await auto.remind(f"Failed: {e}. Try a simpler approach.")
```

### Periodic reflection
```python
async def main(auto):
    for i in range(100):
        await auto.remind(f"Experiment {i}")
        if (i + 1) % 10 == 0:
            await auto.remind("Reflect on last 10 experiments. Adjust strategy.")
```
```

- [ ] **Step 3: Commit**

```bash
git add skills/
git commit -m "feat: add orchestrate skill definition"
```

---

### Task 5: Integration test — full round trip

**Files:**
- Create: `tests/test_cli_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_cli_integration.py`:

```python
"""Integration test: launch a program, check status, stop it."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrate.cli import cmd_run, cmd_list, cmd_status, cmd_stop, RUNS_DIR


@pytest.fixture(autouse=True)
def clean_runs_dir(tmp_path):
    test_runs = tmp_path / "runs"
    test_runs.mkdir()
    with patch("orchestrate.cli.RUNS_DIR", test_runs):
        yield test_runs


def test_concurrent_runs(clean_runs_dir, tmp_path):
    """Launch two programs, verify list shows both, stop targets correct one."""
    # Write two programs
    p1 = tmp_path / "prog1.py"
    p1.write_text("import time\nasync def main(auto):\n    time.sleep(60)\n")

    p2 = tmp_path / "prog2.py"
    p2.write_text("import time\nasync def main(auto):\n    time.sleep(60)\n")

    id1 = cmd_run(str(p1))
    id2 = cmd_run(str(p2))
    time.sleep(1)

    # List shows both
    runs = cmd_list()
    running = [r for r in runs if r["status"] == "running"]
    assert len(running) >= 2

    # Stop one
    cmd_stop(id1)
    time.sleep(1)

    # Verify only one stopped
    info1 = cmd_status(id1)
    assert info1["alive"] is False

    info2 = cmd_status(id2)
    assert info2["alive"] is True

    # Cleanup
    cmd_stop(id2)


def test_program_completes_and_marks_done(clean_runs_dir, tmp_path):
    """A program that finishes quickly should set status to done."""
    prog = tmp_path / "quick.py"
    prog.write_text("async def main(auto):\n    print('hello')\n")

    run_id = cmd_run(str(prog))
    time.sleep(2)  # wait for completion

    info = cmd_status(run_id)
    assert info["status"] == "done"


def test_program_error_marks_error(clean_runs_dir, tmp_path):
    """A program that raises should set status to error."""
    prog = tmp_path / "bad.py"
    prog.write_text("async def main(auto):\n    raise ValueError('boom')\n")

    run_id = cmd_run(str(prog))
    time.sleep(2)

    info = cmd_status(run_id)
    assert info["status"] == "error"
    assert "boom" in info.get("error", "")
```

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/test_cli_integration.py -v
```

Expected: All pass.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All tests pass (unit + API + CLI + integration).

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_integration.py
git commit -m "test: add CLI integration tests for concurrent runs"
```

---

### Task 6: Verify installability

- [ ] **Step 1: Clean install and verify CLI entry point**

```bash
pip install -e .
which orchestrate-run
orchestrate-run --help
```

Expected: `orchestrate-run` is in PATH and shows help output.

- [ ] **Step 2: Run a smoke test end-to-end**

Create a temp program and run it:

```bash
cat > /tmp/test_orch.py << 'EOF'
async def main(auto):
    print("orchestrate works!")
EOF
orchestrate-run /tmp/test_orch.py
sleep 2
orchestrate-run list
orchestrate-run stop --all
```

Expected: Run starts, list shows it, stop cleans up.

- [ ] **Step 3: Commit any fixes**

If anything needed adjusting, commit.

```bash
git add -A
git commit -m "fix: address install/smoke test issues"
```
