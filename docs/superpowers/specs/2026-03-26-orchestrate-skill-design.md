# Design: orchestrate skill

## Overview

A Claude Code skill that teaches the model to write Python programs using the `orchestrate` library for programmatic control of Claude agents. Programs run directly via the Agent SDK — no stop hooks, no sidecar processes, no IPC.

Covers: loops, optimization, research, multi-agent coordination, iterative workflows — anything requiring programmatic control over agent execution.

## Architecture

```
User describes task
  → Skill triggers
  → Model writes Python file (any name)
  → Model runs: orchestrate-run <file.py>
  → CLI launches background process
  → Program calls auto.remind() / auto.task() via Agent SDK
  → Model monitors: orchestrate-run status
  → Model stops: orchestrate-run stop
```

## Components

### 1. Skill definition (`skills/orchestrate/SKILL.md`)

Frontmatter with name + description (trigger keywords). Body teaches the model:

- How to write a program: `async def main(auto)` using `from orchestrate import Auto`
- API: `auto.remind(instruction, schema=None)`, `auto.task(instruction, to, schema=None)`, `auto.agent(name, cwd=None)`
- How to launch: `orchestrate-run <file.py>` (returns a run ID)
- How to list runs: `orchestrate-run list`
- How to monitor: `orchestrate-run status <id>`, `orchestrate-run log <id>`
- How to stop: `orchestrate-run stop <id>`, `orchestrate-run stop --all`
- Patterns: optimization loops, multi-agent, error recovery, periodic reflection
- State tracking via `from orchestrate import state`

Trigger keywords: "orchestrate", "run a loop", "multi-agent", "keep improving", "optimize", "iterate", or when a Python file with `async def main(auto)` exists.

### 2. CLI wrapper (`src/orchestrate/cli.py`)

Thin CLI managing multiple concurrent background processes. Entry point: `orchestrate-run`.

Each run gets a short ID (first 4 chars of a uuid) for easy reference.

**Commands:**

#### `orchestrate-run <file.py>`
1. Resolve file path
2. Generate run ID (4-char short uuid)
3. Create run directory: `~/.orchestrate/runs/<id>/`
4. Write `run.json`: `{id, pid, file, start_time, status: "running"}`
5. Fork background process:
   - Import user module
   - Find `async def main(auto)` (or `async def main(step)` for compat)
   - Create `Auto()` instance
   - Call `asyncio.run(main(auto))`
   - On completion/error, update `run.json` status to "done"/"error"
6. Stream stdout/stderr to `~/.orchestrate/runs/<id>/output.log`
7. Print run ID + log path, return immediately

#### `orchestrate-run list`
1. Scan all directories in `~/.orchestrate/runs/`
2. Read each `run.json`, check if PID is alive
3. Print table: ID, FILE, STATUS, STARTED

Example output:
```
ID        FILE              STATUS    STARTED
a3f1      optimize.py       running   2 min ago
b7c2      research.py       running   15 min ago
c9d0      sweep.py          done      1 hour ago
```

#### `orchestrate-run status <id>`
1. Read `~/.orchestrate/runs/<id>/run.json`
2. Check if PID is alive (`os.kill(pid, 0)`)
3. Print: status, file, elapsed time
4. Tail last 20 lines of `output.log`
5. If no `<id>` given, behave like `list`

#### `orchestrate-run stop <id>`
1. Read PID from `~/.orchestrate/runs/<id>/run.json`
2. Send SIGTERM, wait 3s, SIGKILL if needed
3. Update `run.json` status to "stopped"
4. `orchestrate-run stop --all` kills all running processes

#### `orchestrate-run log <id>`
1. Tail the `output.log` file for the given run ID
2. Streams live output (like `tail -f`)

### 3. Source restructure

Move library from `orchestrate/` to `src/orchestrate/`:

```
orchestrate/
├── src/
│   └── orchestrate/
│       ├── __init__.py      # exports Auto, state
│       ├── core.py          # Auto class with remind(), task(), agent()
│       ├── state.py         # persistent key-value store
│       └── cli.py           # (NEW) orchestrate-run CLI
├── skills/
│   └── orchestrate/
│       └── SKILL.md         # (NEW) skill definition
├── api/
│   └── server.py            # (UPDATE imports)
├── tests/                   # (UPDATE imports)
├── pyproject.toml            # (UPDATE)
└── ...
```

### 4. pyproject.toml changes

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "orchestrate"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["claude-agent-sdk"]

[project.scripts]
orchestrate-run = "orchestrate.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

## State directory layout

```
~/.orchestrate/
└── runs/
    ├── a3f1/
    │   ├── run.json       # {id, pid, file, start_time, status, error?}
    │   └── output.log     # stdout + stderr from the program
    ├── b7c2/
    │   ├── run.json
    │   └── output.log
    └── c9d0/
        ├── run.json
        └── output.log
```

## Program API

The model writes programs like:

```python
async def main(auto):
    # remind() = send instruction to self, returns response
    result = await auto.remind("Run tests and report results")

    # remind() with schema = returns parsed dict
    metrics = await auto.remind(
        "Run train.py, report val_loss",
        schema={"val_loss": "float"}
    )

    # task() = dispatch to named agent
    auto.agent("researcher", cwd="/path/to/research")
    findings = await auto.task("Survey papers on X", to="researcher")

    # state tracking
    from orchestrate import state
    state.set("best_loss", metrics["val_loss"])
```

No imports needed for the `auto` object — it's passed to `main()` by the CLI.

## What this does NOT include

- No stop hooks or IPC
- No integration with current Claude Code conversation (agents run as separate SDK sessions)
- No agent-ui integration for CLI monitoring
- No `setup` command (no hooks to install)

## Installation

```bash
npx skills add signalrush/orchestrate
```

Registers the skill with Claude Code and makes `orchestrate-run` available in PATH.

## Testing

- Update existing 18 unit tests to use `src/` import paths
- Update API server imports
- Add CLI tests: verify run/list/status/stop/log commands work with a mock program
- Test concurrent runs: launch two programs, verify `list` shows both, `stop` targets correct one
