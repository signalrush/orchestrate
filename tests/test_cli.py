"""Tests for the orchestrate-run CLI."""

import json
import os
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
    prog = path / "test_prog.py"
    prog.write_text(f"async def main(auto):\n    {body}\n")
    return prog


def test_run_creates_run_dir_and_json(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path)
    run_id = cmd_run(str(prog))
    assert len(run_id) == 8
    run_dir = clean_runs_dir / run_id
    assert run_dir.exists()
    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json["id"] == run_id
    assert run_json["file"] == str(prog)
    assert run_json["status"] in ("running", "done")
    assert "pid" in run_json


def test_run_creates_output_log(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path)
    run_id = cmd_run(str(prog))
    time.sleep(1)
    log_file = clean_runs_dir / run_id / "output.log"
    assert log_file.exists()


def test_list_shows_runs(clean_runs_dir):
    for rid, status in [("ab12", "running"), ("cd34", "done")]:
        d = clean_runs_dir / rid
        d.mkdir()
        (d / "run.json").write_text(
            json.dumps(
                {
                    "id": rid,
                    "pid": 99999,
                    "file": f"{rid}.py",
                    "start_time": time.time(),
                    "status": status,
                }
            )
        )
        (d / "output.log").write_text("")
    runs = cmd_list()
    assert len(runs) == 2
    ids = {r["id"] for r in runs}
    assert ids == {"ab12", "cd34"}


def test_status_returns_run_info(clean_runs_dir):
    rid = "ef56"
    d = clean_runs_dir / rid
    d.mkdir()
    (d / "run.json").write_text(
        json.dumps(
            {
                "id": rid,
                "pid": os.getpid(),
                "file": "test.py",
                "start_time": time.time(),
                "status": "running",
            }
        )
    )
    (d / "output.log").write_text("line1\nline2\n")
    info = cmd_status(rid)
    assert info["id"] == rid
    assert info["alive"] is True


def test_stop_kills_process(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path, body="import time; time.sleep(60)")
    run_id = cmd_run(str(prog))
    time.sleep(1)
    run_json = json.loads((clean_runs_dir / run_id / "run.json").read_text())
    pid = run_json["pid"]
    cmd_stop(run_id)
    time.sleep(1)
    # Process should be dead
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False
    assert not alive


def test_program_completes_marks_done(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path, body="print('hello')")
    run_id = cmd_run(str(prog))
    time.sleep(2)
    info = cmd_status(run_id)
    assert info["status"] == "done"


def test_program_error_marks_error(clean_runs_dir, tmp_path):
    prog = _write_test_program(tmp_path, body="raise ValueError('boom')")
    run_id = cmd_run(str(prog))
    for _ in range(20):
        time.sleep(0.5)
        info = cmd_status(run_id)
        if info["status"] != "running":
            break
    assert info["status"] == "error"
    assert "boom" in info.get("error", "")
