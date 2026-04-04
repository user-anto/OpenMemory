import pytest

from memory import engine
from memory.store.file_store import FileStore


def _new_store(tmp_path):
    return FileStore(root=tmp_path / "store")


def _commit(store, message, summary, topics, extra_message):
    engine.stage(
        store,
        messages=[{"role": "assistant", "content": extra_message}],
        model="m",
        provider="p",
        topics=topics,
    )
    return engine.commit(
        store,
        message=message,
        summary=summary,
        model="m",
        provider="p",
        topics=topics,
    ).sha


def test_search_returns_relevant_hits(tmp_path):
    store = _new_store(tmp_path)
    _commit(store, "init", "database design started", ["db"], "created schema")
    target_sha = _commit(
        store,
        "auth finalized",
        "jwt refresh token approach selected",
        ["auth", "jwt"],
        "token refresh flow implemented",
    )
    _commit(store, "ui tweaks", "button colors updated", ["ui"], "styling only")

    result = engine.search(store, query="jwt refresh", limit=3)
    assert result.query == "jwt refresh"
    assert len(result.hits) >= 1
    assert target_sha in [h.sha for h in result.hits]


def test_search_validation_and_empty_limit(tmp_path):
    store = _new_store(tmp_path)
    _commit(store, "init", "seed", ["x"], "x")

    with pytest.raises(ValueError):
        engine.search(store, query="   ")

    result = engine.search(store, query="seed", limit=0)
    assert result.hits == []
