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
- How to launch: `orchestrate-run <file.py>`
- How to monitor: `orchestrate-run status`
- How to stop: `orchestrate-run stop`
- Patterns: optimization loops, multi-agent, error recovery, periodic reflection
- State tracking via `from orchestrate import state`

Trigger keywords: "orchestrate", "run a loop", "multi-agent", "keep improving", "optimize", "iterate", or when a Python file with `async def main(auto)` exists.

### 2. CLI wrapper (`src/orchestrate/cli.py`)

Thin CLI managing background processes. Entry point: `orchestrate-run`.

**Commands:**

#### `orchestrate-run <file.py>`
1. Resolve file path
2. Create run directory: `~/.orchestrate/<timestamp>/`
3. Update `~/.orchestrate/latest` symlink
4. Write `run.json`: `{pid, file, start_time, status: "running"}`
5. Fork background process:
   - Import user module
   - Find `async def main(auto)` (or `async def main(step)` for compat)
   - Create `Auto()` instance
   - Call `asyncio.run(main(auto))`
   - On completion/error, update `run.json` status to "done"/"error"
6. Stream stdout/stderr to `~/.orchestrate/latest/output.log`
7. Print PID + log path, return immediately

#### `orchestrate-run status`
1. Read `~/.orchestrate/latest/run.json`
2. Check if PID is alive (`os.kill(pid, 0)`)
3. Print: status, file, elapsed time
4. Tail last 20 lines of `output.log`

#### `orchestrate-run stop`
1. Read PID from `~/.orchestrate/latest/run.json`
2. Send SIGTERM, wait 3s, SIGKILL if needed
3. Update `run.json` status to "stopped"

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
├── latest -> ./1711500000/
├── 1711500000/
│   ├── run.json       # {pid, file, start_time, status, error?}
│   └── output.log     # stdout + stderr from the program
└── 1711499000/
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
- Add CLI tests: verify run/status/stop commands work with a mock program
