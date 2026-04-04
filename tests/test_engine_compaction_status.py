import asyncio
import time

from memory import engine
from memory.store.file_store import FileStore


def _new_store(tmp_path):
    return FileStore(root=tmp_path / "store")


def _seed_large_commit(store, n=25):
    msgs = [{"role": "user", "content": f"msg {i}"} for i in range(n)]
    engine.stage(store, messages=msgs, model="m", provider="p")
    return engine.commit(
        store,
        message="large",
        summary="large summary",
        model="m",
        provider="p",
    ).sha


def test_compaction_status_lifecycle_and_mutation_exception(tmp_path, monkeypatch):
    store = _new_store(tmp_path)
    sha = _seed_large_commit(store, n=25)

    async def fake_call_llm(prompt: str) -> str:
        await asyncio.sleep(0.15)
        return "compressed summary"

    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

    started = engine.compact(store)
    assert started.status == "compaction_started"
    assert started.sha == sha

    # Duplicate run guard while background task is active.
    duplicate = engine.compact(store)
    assert duplicate.status in {"already_running", "compaction_started"}

    deadline = time.time() + 5
    while time.time() < deadline:
        state = engine.compaction_status(store)
        if state.status in {"succeeded", "failed"}:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Compaction did not finish in time")

    final = engine.compaction_status(store)
    assert final.status == "succeeded"
    assert final.sha == sha

    compacted = engine.read_commit(store, sha)
    assert compacted.tree.metadata.compacted is True
    assert compacted.tree.metadata.compacted_up_to_index == 5
    assert len(compacted.tree.messages) == 21
    assert compacted.tree.messages[0].role == "system"
