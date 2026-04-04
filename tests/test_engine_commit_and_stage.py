from memory import engine
from memory.store.file_store import FileStore


def _new_store(tmp_path):
    return FileStore(root=tmp_path / "store")


def test_stage_and_commit_deterministic_merge_and_reset(tmp_path):
    store = _new_store(tmp_path)

    engine.stage(
        store,
        messages=[
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ],
        model="m1",
        provider="p1",
        decisions=[{"id": "d1", "decision": "Use SQLite", "rationale": "Simple"}],
        open_questions=["How to deploy?"],
        artifacts=[{"type": "file", "path": "schema.sql", "description": "DB schema"}],
        topics=["db", "design"],
    )
    c1 = engine.commit(
        store,
        message="initial",
        summary="Initial summary",
        model="m1",
        provider="p1",
        topics=["db"],
    )
    first = engine.read_commit(store, c1.sha)

    engine.stage(
        store,
        messages=[
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
        ],
        model="m2",
        provider="p2",
        decisions=[{"id": "d2", "decision": "Add cache", "rationale": "Latency"}],
        open_questions=["How to deploy?", "How to monitor?"],
        topics=["design", "perf", "perf"],
    )
    c2 = engine.commit(
        store,
        message="second",
        summary="Second summary",
        model="m2",
        provider="p2",
        topics=["release", "perf"],
    )

    second = engine.read_commit(store, c2.sha)

    assert [t for t in second.tree.context.topics] == ["db", "design", "perf", "release"]
    assert second.tree.context.open_questions == ["How to deploy?", "How to monitor?"]
    assert [d.id for d in second.tree.context.decisions] == ["d1", "d2"]

    # Commit immutability in normal flows: earlier commit is unchanged.
    reread_first = engine.read_commit(store, c1.sha)
    assert len(reread_first.tree.messages) == len(first.tree.messages) == 2
    assert reread_first.tree.context.summary == "Initial summary"

    # Session staged area resets after commit.
    session = store.read_session()
    assert session is not None
    assert session.staged_messages == []
    assert session.staged_decisions == []
    assert session.staged_open_questions == []
    assert session.staged_artifacts == []
    assert session.staged_topics == []
