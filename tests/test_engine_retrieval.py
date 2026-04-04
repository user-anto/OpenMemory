import pytest

from memory import engine
from memory.store.file_store import FileStore


def _new_store(tmp_path):
    return FileStore(root=tmp_path / "store")


def _seed(store):
    msgs = [
        {"role": "user", "content": "A" * 5},
        {"role": "assistant", "content": "B" * 10},
        {"role": "user", "content": "C" * 15},
        {"role": "assistant", "content": "D" * 20},
    ]
    engine.stage(store, messages=msgs, model="m", provider="p")
    return engine.commit(
        store,
        message="seed",
        summary="seed",
        model="m",
        provider="p",
    ).sha


def test_read_window_last_n_and_before_index(tmp_path):
    store = _new_store(tmp_path)
    sha = _seed(store)

    w1 = engine.read_window(store, sha=sha, last_n=2)
    assert w1.total_messages == 4
    assert (w1.start_index, w1.end_index) == (2, 4)
    assert [m.content for m in w1.messages] == ["C" * 15, "D" * 20]

    w2 = engine.read_window(store, sha=sha, last_n=2, before_index=3)
    assert (w2.start_index, w2.end_index) == (1, 3)
    assert [m.content for m in w2.messages] == ["B" * 10, "C" * 15]


def test_read_window_validation(tmp_path):
    store = _new_store(tmp_path)
    _seed(store)
    with pytest.raises(ValueError):
        engine.read_window(store, last_n=0)


def test_read_for_budget_newest_first_contiguous(tmp_path):
    store = _new_store(tmp_path)
    sha = _seed(store)

    # Newest messages are length 20 and 15. Budget 30 should include only the newest one.
    b = engine.read_for_budget(store, sha=sha, max_chars=30)
    assert b.total_messages == 4
    assert [m.content for m in b.messages] == ["D" * 20]
    assert b.used_chars == 20

    # Tiny budget still returns the newest message for continuity.
    tiny = engine.read_for_budget(store, sha=sha, max_chars=3)
    assert len(tiny.messages) == 1
    assert tiny.messages[0].content == "D" * 20


def test_read_for_budget_validation(tmp_path):
    store = _new_store(tmp_path)
    _seed(store)
    with pytest.raises(ValueError):
        engine.read_for_budget(store, max_chars=0)
