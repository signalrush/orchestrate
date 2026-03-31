import json
import os
import pytest
from pathlib import Path


def _multiprocess_worker(args):
    path, i = args
    import os
    os.chdir(str(path))
    from orchestrate import state
    state.set(str(i), i)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Run each test in a temp directory so state files don't collide."""
    monkeypatch.chdir(tmp_path)
    yield
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


# TS-1: Corrupt JSON raises (documents behavior — NOT silent data loss)
def test_corrupt_file_raises():
    Path("orchestrate-state.json").write_text("{bad json")
    from orchestrate import state
    with pytest.raises(json.JSONDecodeError):
        state.get()


# TS-2: Non-serializable value raises, temp file cleaned up, original state preserved
def test_nonserializable_raises_and_cleans_up(tmp_path):
    from orchestrate import state
    state.set("x", 1)
    with pytest.raises(TypeError):
        state.set("obj", object())
    assert state.get("x") == 1
    assert list(tmp_path.glob(".orchestrate-state-*.tmp")) == []


# TS-3: os.rename failure cleans up temp file, original state preserved
def test_rename_failure_cleans_up(tmp_path):
    import unittest.mock as mock
    from orchestrate import state
    state.set("x", 1)
    with mock.patch("os.rename", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            state.set("x", 2)
    assert state.get("x") == 1
    assert list(tmp_path.glob(".orchestrate-state-*.tmp")) == []


# TS-4: Concurrent writes from multiple processes — no keys lost
def test_concurrent_multiprocess_writes(tmp_path):
    import multiprocessing

    with multiprocessing.Pool(8) as p:
        p.map(_multiprocess_worker, [(tmp_path, i) for i in range(40)])

    from orchestrate import state
    result = state.get()
    assert len(result) == 40
    assert all(result[str(i)] == i for i in range(40))


# TS-5: modifier exception leaves state unchanged (rollback semantics)
def test_modifier_exception_rolls_back():
    from orchestrate import state
    state.set("safe", 99)
    with pytest.raises(RuntimeError):
        def boom(s):
            raise RuntimeError("fail")
        state._read_modify_write(boom)
    assert state.get("safe") == 99


# TS-6: Concurrent threaded reads return consistent values
def test_concurrent_reads_consistent():
    import threading
    from orchestrate import state
    state.set("v", 42)
    results = []
    lock = threading.Lock()

    def reader():
        val = state.get("v")
        with lock:
            results.append(val)

    threads = [threading.Thread(target=reader) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == 42 for r in results)


# TS-7: Deeply nested structures round-trip without mutation
def test_nested_round_trip():
    from orchestrate import state
    nested = {"a": {"b": [1, 2, {"c": True, "d": None}]}}
    state.set("deep", nested)
    assert state.get("deep") == nested


# TS-8: get() with no state file returns empty dict
def test_get_all_no_file_returns_empty():
    from orchestrate import state
    assert state.get() == {}


# Additional basic type tests
def test_set_get_string():
    from orchestrate import state
    state.set("name", "hello")
    assert state.get("name") == "hello"


def test_set_get_int():
    from orchestrate import state
    state.set("count", 7)
    assert state.get("count") == 7


def test_set_get_list():
    from orchestrate import state
    state.set("items", [1, 2, 3])
    assert state.get("items") == [1, 2, 3]


def test_set_get_dict():
    from orchestrate import state
    state.set("meta", {"k": "v"})
    assert state.get("meta") == {"k": "v"}


def test_set_get_bool():
    from orchestrate import state
    state.set("flag", True)
    assert state.get("flag") is True


def test_set_get_none_value():
    from orchestrate import state
    state.set("nothing", None)
    assert state.get("nothing") is None


def test_file_created_on_first_write(tmp_path):
    from orchestrate import state
    assert not (tmp_path / "orchestrate-state.json").exists()
    state.set("k", 1)
    assert (tmp_path / "orchestrate-state.json").exists()


def test_concurrent_threaded_writes(tmp_path):
    import threading
    from orchestrate import state

    def writer(i):
        state.set(str(i), i)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = state.get()
    assert len(result) == 20
    assert all(result[str(i)] == i for i in range(20))


def test_empty_state_file_returns_empty():
    Path("orchestrate-state.json").write_text("")
    from orchestrate import state
    assert state.get() == {}
