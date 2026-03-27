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
    p1 = tmp_path / "prog1.py"
    p1.write_text("import time\nasync def main(auto):\n    time.sleep(60)\n")

    p2 = tmp_path / "prog2.py"
    p2.write_text("import time\nasync def main(auto):\n    time.sleep(60)\n")

    id1 = cmd_run(str(p1))
    id2 = cmd_run(str(p2))
    time.sleep(1)

    # List shows both
    runs = cmd_list()
    running_ids = {r["id"] for r in runs if r["status"] == "running"}
    assert id1 in running_ids
    assert id2 in running_ids

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
    time.sleep(2)

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
