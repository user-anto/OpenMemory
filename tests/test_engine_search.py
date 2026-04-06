import pytest

from memory import engine
from memory.models import SearchIndexData
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


# ── Index-specific tests ─────────────────────────────────────────────────────


def test_index_populated_on_commit(tmp_path):
    """Index file should exist and contain the commit after committing."""
    store = _new_store(tmp_path)
    sha = _commit(store, "setup caching", "redis caching layer", ["cache"], "added redis")

    index = store.read_search_index()
    assert index.head_sha == sha
    assert sha in index.documents
    assert index.documents[sha].message == "setup caching"
    # The term "redis" should appear in postings
    assert "redis" in index.postings
    assert sha in index.postings["redis"]


def test_index_accumulates_across_commits(tmp_path):
    """Each commit should add to the index, not replace it."""
    store = _new_store(tmp_path)
    sha1 = _commit(store, "first", "alpha work", ["alpha"], "content a")
    sha2 = _commit(store, "second", "beta work", ["beta"], "content b")

    index = store.read_search_index()
    assert sha1 in index.documents
    assert sha2 in index.documents
    assert index.head_sha == sha2


def test_rebuild_index_from_scratch(tmp_path):
    """rebuild_index should produce a valid index from existing commits."""
    store = _new_store(tmp_path)
    sha1 = _commit(store, "one", "first commit", ["topic1"], "hello world")
    sha2 = _commit(store, "two", "second commit", ["topic2"], "goodbye world")

    # Wipe the index
    store.write_search_index(SearchIndexData())
    assert store.read_search_index().head_sha is None

    # Rebuild
    index = engine.rebuild_index(store)
    assert index.head_sha == sha2
    assert sha1 in index.documents
    assert sha2 in index.documents
    assert len(index.postings) > 0


def test_search_works_after_index_deleted(tmp_path):
    """Search should gracefully rebuild when the index is missing."""
    store = _new_store(tmp_path)
    target_sha = _commit(
        store, "deploy pipeline", "CI/CD with docker", ["devops"], "dockerfile created"
    )

    # Delete the index file
    store.write_search_index(SearchIndexData())

    # Search should still work (triggers rebuild)
    result = engine.search(store, query="docker", limit=5)
    assert len(result.hits) >= 1
    assert target_sha in [h.sha for h in result.hits]

    # Index should now be repopulated
    index = store.read_search_index()
    assert index.head_sha is not None
    assert target_sha in index.documents


def test_indexed_search_matches_top_hit(tmp_path):
    """The indexed search should return the same top hit as the brute-force."""
    store = _new_store(tmp_path)
    _commit(store, "init", "project setup", ["setup"], "initial files")
    _commit(store, "auth system", "oauth2 with google", ["auth", "oauth"], "google sso")
    _commit(store, "logging", "structured logging added", ["infra"], "pino logger")

    indexed_result = engine.search(store, query="oauth google", limit=3)

    # Also run brute-force directly for comparison
    terms = ["oauth", "google"]
    brute_result = engine._search_brute_force(store, "oauth google", terms, 3)

    assert len(indexed_result.hits) >= 1
    assert len(brute_result.hits) >= 1
    assert indexed_result.hits[0].sha == brute_result.hits[0].sha


def test_search_no_matches(tmp_path):
    """Searching for a term that doesn't exist should return empty hits."""
    store = _new_store(tmp_path)
    _commit(store, "init", "basic setup", ["setup"], "hello")

    result = engine.search(store, query="zzzznonexistent", limit=5)
    assert result.hits == []
