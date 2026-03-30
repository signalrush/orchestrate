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
