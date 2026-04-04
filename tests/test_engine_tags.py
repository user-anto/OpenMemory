import pytest

from memory import engine
from memory.store.file_store import FileStore


def _new_store(tmp_path):
    return FileStore(root=tmp_path / "store")


def _seed_commit(store):
    engine.stage(
        store,
        messages=[{"role": "user", "content": "hello"}],
        model="m",
        provider="p",
    )
    return engine.commit(
        store,
        message="seed",
        summary="seed summary",
        model="m",
        provider="p",
    ).sha


def test_create_and_list_and_resolve_tags(tmp_path):
    store = _new_store(tmp_path)
    sha = _seed_commit(store)

    t = engine.create_tag(store, "v1", sha=None)
    assert t.name == "v1"
    assert t.sha == sha

    t2 = engine.create_tag(store, "release_1", sha=sha)
    assert t2.sha == sha

    listed = engine.list_tags(store)
    assert [x.name for x in listed.tags] == ["release_1", "v1"]

    by_tag = engine.read_commit(store, "v1")
    assert by_tag.sha == sha


def test_invalid_tag_name_rejected(tmp_path):
    store = _new_store(tmp_path)
    _seed_commit(store)

    with pytest.raises(ValueError):
        engine.create_tag(store, "../bad")

    with pytest.raises(ValueError):
        engine.create_tag(store, "")


def test_tag_requires_commit(tmp_path):
    store = _new_store(tmp_path)
    with pytest.raises(ValueError):
        engine.create_tag(store, "v1")
