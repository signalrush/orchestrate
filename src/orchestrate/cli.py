"""orchestrate-run CLI — manage concurrent background orchestrate programs."""

import argparse
import asyncio
import importlib.util
import inspect
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path

RUNS_DIR = Path.home() / ".orchestrate" / "runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def _read_run_json(run_id: str) -> dict:
    return json.loads((_run_dir(run_id) / "run.json").read_text())


def _write_run_json(run_id: str, data: dict) -> None:
    (_run_dir(run_id) / "run.json").write_text(json.dumps(data))


def _pid_alive(pid: int) -> bool:
    """Return True if pid is alive and not a zombie."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Reap zombie children so they don't appear alive
    try:
        result = os.waitpid(pid, os.WNOHANG)
        if result[0] == pid:
            return False
    except ChildProcessError:
        # Not our child — check via ps
        import subprocess as _sp
        r = _sp.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        if r.returncode != 0:
            return False
        stat = r.stdout.strip()
        if stat.startswith("Z"):
            return False
    except OSError:
        return False
    return True


def _elapsed(start_time: float) -> str:
    secs = int(time.time() - start_time)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


# ---------------------------------------------------------------------------
# Internal _exec command — runs inside the background subprocess
# ---------------------------------------------------------------------------

class _LazyAuto:
    """Proxy that imports and instantiates orchestrate.core.Auto on first use."""

    def __init__(self) -> None:
        self._real: object | None = None

    def _get(self) -> object:
        if self._real is None:
            from orchestrate.core import Auto
            api_url = os.environ.get("ORCHESTRATE_API_URL")
            session_id = os.environ.get("ORCHESTRATE_SESSION_ID")
            self._real = Auto(api_url=api_url, session_id=session_id)
        return self._real

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


def _exec_program(file_path: str, run_id: str, run_dir_path: str) -> None:
    """Import and run user's async main(auto) in-process. Updates run.json on finish."""
    run_dir = Path(run_dir_path)
    data = json.loads((run_dir / "run.json").read_text())

    try:
        spec = importlib.util.spec_from_file_location("_user_program", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        main_fn = getattr(module, "main", None)
        if main_fn is None or not inspect.iscoroutinefunction(main_fn):
            raise ValueError("No async def main() found in program")

        sig = inspect.signature(main_fn)
        params = list(sig.parameters.keys())

        auto = _LazyAuto()

        if params:
            coro = main_fn(auto)
        else:
            coro = main_fn()

        asyncio.run(coro)

        data["status"] = "done"
    except Exception as exc:
        data["status"] = "error"
        data["error"] = str(exc)
    finally:
        (run_dir / "run.json").write_text(json.dumps(data))
        # Signal the API that the program is done
        api_url = os.environ.get("ORCHESTRATE_API_URL")
        session_id = os.environ.get("ORCHESTRATE_SESSION_ID")
        if api_url and session_id:
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{api_url}/sessions/{session_id}/program-done",
                    method="POST",
                    data=b"",
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

def cmd_run(file_path: str) -> str:
    """Launch file_path as a background process. Returns run ID."""
    abs_path = str(Path(file_path).resolve())
    run_id = uuid.uuid4().hex[:4]

    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    log_path = run_dir / "output.log"
    data = {
        "id": run_id,
        "pid": None,
        "file": abs_path,
        "start_time": time.time(),
        "status": "running",
    }
    _write_run_json(run_id, data)

    log_fd = open(log_path, "w")
    proc = __import__("subprocess").Popen(
        [sys.executable, __file__, "_exec", abs_path, run_id, str(run_dir)],
        stdout=log_fd,
        stderr=log_fd,
        close_fds=True,
    )
    log_fd.close()

    data["pid"] = proc.pid
    _write_run_json(run_id, data)

    print(f"Started run {run_id}  log: {log_path}")
    print("Program running in background. STOP HERE — do not monitor or wait. The program will send remind messages through the queue.")
    return run_id


def cmd_list() -> list[dict]:
    """List all runs. Returns list of run info dicts."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for entry in sorted(RUNS_DIR.iterdir()):
        json_file = entry / "run.json"
        if not json_file.exists():
            continue
        data = json.loads(json_file.read_text())
        data["alive"] = _pid_alive(data.get("pid", 0))
        runs.append(data)

    if not runs:
        print("No runs found.")
        return runs

    print(f"{'ID':<6} {'FILE':<40} {'STATUS':<10} {'STARTED'}")
    for r in runs:
        started = time.strftime("%H:%M:%S", time.localtime(r["start_time"]))
        print(f"{r['id']:<6} {r['file']:<40} {r['status']:<10} {started}")

    return runs


def cmd_status(run_id: str | None = None) -> dict | list[dict]:
    """Print status for a run (or list all if no id given)."""
    if run_id is None:
        return cmd_list()

    data = _read_run_json(run_id)
    alive = _pid_alive(data.get("pid", 0))
    data["alive"] = alive

    print(f"ID:      {data['id']}")
    print(f"File:    {data['file']}")
    print(f"Status:  {data['status']}")
    print(f"Elapsed: {_elapsed(data['start_time'])}")
    print(f"PID:     {data['pid']} ({'alive' if alive else 'dead'})")

    log_path = _run_dir(run_id) / "output.log"
    if log_path.exists():
        lines = log_path.read_text().splitlines()
        tail = lines[-20:]
        if tail:
            print("\n--- last 20 lines of output.log ---")
            print("\n".join(tail))

    return data


def cmd_stop(run_id: str | None = None, stop_all: bool = False) -> None:
    """Stop a run (or all running runs if stop_all=True)."""
    if stop_all:
        runs = cmd_list()
        for r in runs:
            if r["status"] == "running":
                cmd_stop(r["id"])
        return

    if run_id is None:
        print("Error: provide a run ID or --all")
        return

    data = _read_run_json(run_id)
    pid = data.get("pid")
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                time.sleep(0.1)
                if not _pid_alive(pid):
                    break
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        # Reap the child process so it doesn't linger as a zombie
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        except OSError:
            pass

    data["status"] = "stopped"
    _write_run_json(run_id, data)
    print(f"Stopped run {run_id}")


def cmd_log(run_id: str) -> None:
    """Show last 50 lines of output.log for a run (non-blocking)."""
    log_path = _run_dir(run_id) / "output.log"
    if not log_path.exists():
        print(f"No log file for run {run_id}")
        return
    lines = log_path.read_text().splitlines()
    for line in lines[-50:]:
        print(line)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    # Internal _exec subcommand — must be first check
    if argv and argv[0] == "_exec":
        _exec_program(argv[1], argv[2], argv[3])
        return

    parser = argparse.ArgumentParser(prog="orchestrate-run")
    sub = parser.add_subparsers(dest="command")

    # run subcommand
    run_p = sub.add_parser("run", help="Run a program file")
    run_p.add_argument("file", help="Python file to run")

    # list subcommand
    sub.add_parser("list", help="List all runs")

    # status subcommand
    status_p = sub.add_parser("status", help="Show run status")
    status_p.add_argument("id", nargs="?", help="Run ID")

    # stop subcommand
    stop_p = sub.add_parser("stop", help="Stop a run")
    stop_p.add_argument("id", nargs="?", help="Run ID")
    stop_p.add_argument("--all", action="store_true", dest="all", help="Stop all running")

    # log subcommand
    log_p = sub.add_parser("log", help="Tail log for a run")
    log_p.add_argument("id", help="Run ID")

    # Detect shorthand: orchestrate-run file.py
    if argv and argv[0].endswith(".py") and not argv[0].startswith("-"):
        cmd_run(argv[0])
        return

    args = parser.parse_args(argv)

    if args.command in (None, "run"):
        if args.command is None:
            parser.print_help()
            return
        cmd_run(args.file)
    elif args.command == "list":
        cmd_list()
    elif args.command == "status":
        cmd_status(args.id)
    elif args.command == "stop":
        cmd_stop(args.id, stop_all=args.all)
    elif args.command == "log":
        cmd_log(args.id)


if __name__ == "__main__":
    main()
