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
