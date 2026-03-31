---
name: orchestrate
description: Run yourself in a loop with programmatic control via the Agent SDK. Use for long-running tasks like optimization, research, iterative improvement, multi-agent coordination, or any multi-step workflow where you need to repeat, branch, or track progress.
---

# Orchestrate

Write a Python program that drives agent execution. The program is your body — agents are your hands. You write the loop, they do the work.

A single program can coordinate 100+ agents — fan out work with `asyncio.gather`, manage worker pools with queues, and stay in control through structured decisions.

## Launch

1. Write a Python program
2. Run: `python <file.py>`

**CRITICAL: After launching with `python`, STOP. Do not monitor, tail logs, or wait. Your turn is done. The program handles the rest.**

## Agent API

```python
from orchestrate import Agent

# Create agents — loads config from ~/.claude/agents/ if available
researcher = Agent("research")                    # loads ~/.claude/agents/research.md
dev = Agent("frontend_dev", prompt="React expert") # inline config
reviewer = Agent("reviewer")                       # loads ~/.claude/agents/reviewer.md

# Run a task
result = await researcher.arun("analyze the codebase")

# With explicit context
impl = await dev.arun("implement feature", context=[result])

# With schema for structured output
review = await reviewer.arun("review this", context=[impl], schema={
    "status": "str", "issues": "list"
})
print(review["status"])  # "APPROVED"

# Spawn a child agent — inherits parent config
helper = dev.spawn("css_helper", prompt="Handle CSS only")
await helper.arun("fix spacing", context=[impl])

# Close when done
await researcher.aclose()
```

### Agent creation

```python
# From ~/.claude/agents/ definition (auto-loads prompt, tools, model)
researcher = Agent("research")

# Inline definition
dev = Agent("my_dev", prompt="You are a developer.", model="sonnet")

# Override a .claude/agents/ definition
fast = Agent("research", model="haiku")  # keeps prompt/tools, overrides model

# Explicit server URL (default: ORCHESTRATE_API_URL env or localhost:7777)
agent = Agent("research", api_url="http://localhost:7777")
```

### Concurrency — just asyncio

```python
# Parallel fan-out
r1, r2 = await asyncio.gather(
    Agent("research").arun("check GitHub"),
    Agent("research").arun("check papers"),
)

# Sequential — agent accumulates session context
await dev.arun("set up project")
await dev.arun("add auth")   # dev remembers the setup
await dev.arun("write tests") # dev remembers everything
```

## Legacy API (still works)

The `Orchestrate` class still works for backward compat:

```python
from orchestrate import Orchestrate

async def main(orch):
    await orch.agent("coder", prompt="...")
    result = await orch.run("do X", to="coder")
```

## Writing effective programs

### Stay in the loop

You are the brain. Agents are the hands. If you don't check their work, you're blind.

```python
# Bad — fire and forget
for task in tasks:
    await dev.arun(task)

# Good — check results, steer
for task in tasks:
    result = await dev.arun(task)
    review = await reviewer.arun(f"Review: {result.text}", schema={"approved": "bool", "feedback": "str"})
    if not review["approved"]:
        await dev.arun(f"Fix: {review['feedback']}", context=[review])
```

### Use schema for program logic

```python
# Structured — reliable
result = await agent.arun("check build status", schema={
    "passing": "bool", "failures": "int", "summary": "str",
})
if result["passing"]:
    print("Ship it")
```

### Use context to chain agent work

Every `arun()` returns a `ContextResult` — pass results between agents with `context=`:

```python
research = await researcher.arun("research X")
impl = await dev.arun("implement based on research", context=[research])
review = await reviewer.arun("review", context=[research, impl])
```

`ContextResult` behaves like a dict for schema fields:
```python
r = await agent.arun("analyze", schema={"score": "int", "reason": "str"})
print(r["score"])   # 87
print(r.text)       # full response
print(r.summary)    # one-line summary
print(r.file)       # ~/.orchestrate/context/42.md
```

### CRITICAL: Always validate context before passing

Never blindly pass `recall()` results. Inspect first:

```python
past = await orch.recall(agent="researcher", limit=10)
for c in past:
    print(f"  [{c.id}] {c.agent}: {c.summary[:80]}")
relevant = [c for c in past if "architecture" in c.summary.lower()]
await dev.arun("build on this", context=relevant)
```

### Implement → Review → Fix loop

Every implementation should be reviewed by a separate agent (not self-review):

```python
researcher = Agent("research")
dev = Agent("implementer")
reviewer = Agent("reviewer")

# Research
research = await researcher.arun("analyze codebase")

# Implement
impl = await dev.arun("implement feature", context=[research])

# Review → Fix loop (max 2 rounds)
for attempt in range(3):
    review = await reviewer.arun("review this", context=[impl], schema={"status": "str", "issues": "list"})
    if review["status"] == "APPROVED":
        break
    if attempt < 2:
        issues = "\n".join(f"- {i}" for i in review["issues"])
        impl = await dev.arun(f"Fix:\n{issues}", context=[review])
```

## Full example

```python
import asyncio
from orchestrate import Agent

async def main():
    researcher = Agent("research")
    dev = Agent("implementer")
    reviewer = Agent("reviewer")

    # Phase 1: Parallel research
    r1, r2 = await asyncio.gather(
        researcher.arun("research approach A"),
        researcher.arun("research approach B"),
    )

    # Phase 2: Implement
    impl = await dev.arun("implement the best approach", context=[r1, r2])

    # Phase 3: Review loop
    for attempt in range(3):
        review = await reviewer.arun("review", context=[impl], schema={"status": "str", "issues": "list"})
        if review["status"] == "APPROVED":
            break
        impl = await dev.arun(f"fix: {review['issues']}", context=[review])

    print(f"Done! Final review: {review['status']}")

    await researcher.aclose()
    await dev.aclose()
    await reviewer.aclose()

if __name__ == "__main__":
    asyncio.run(main())
```
