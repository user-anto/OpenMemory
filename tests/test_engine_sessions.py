from memory import engine
from memory.store.file_store import FileStore


def _new_store(tmp_path):
    return FileStore(root=tmp_path / "store")


def _commit_one(store, text):
    engine.stage(
        store,
        messages=[{"role": "assistant", "content": text}],
        model="m",
        provider="p",
    )
    return engine.commit(
        store,
        message=text,
        summary=text,
        model="m",
        provider="p",
    ).sha


def test_start_session_unique_and_switch(tmp_path):
    store = _new_store(tmp_path)

    s1 = engine.start_session(store, "Pizza Party")
    assert s1.session.name == "Pizza Party"
    assert s1.session.slug == "pizza-party"

    _commit_one(store, "pizza context")
    st = engine.status(store)
    assert st.session_slug == "pizza-party"

    s2 = engine.start_session(store, "Refactor Sprint")
    assert s2.session.slug == "refactor-sprint"
    st2 = engine.status(store)
    assert st2.head is None


def test_use_session_fuzzy(tmp_path):
    store = _new_store(tmp_path)

    engine.start_session(store, "Pizza Party")
    _commit_one(store, "pizza context")
    engine.start_session(store, "Payments")

    used = engine.use_session(store, "continue pizza")
    assert used.session.slug == "pizza-party"
    assert used.fuzzy_match is True

    commit_obj = engine.read_commit(store)
    assert "pizza" in commit_obj.message


def test_start_session_name_must_be_globally_unique(tmp_path):
    store = _new_store(tmp_path)
    engine.start_session(store, "Pizza Party")
    try:
        engine.start_session(store, "pizza party")
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Expected unique-name validation error")
