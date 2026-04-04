from mcp_server import server as mcp_server
from memory.store.file_store import FileStore


def _seed_with_tools():
    mcp_server.memory_stage(
        messages=[
            {"role": "user", "content": "Find auth decision"},
            {"role": "assistant", "content": "Use JWT refresh tokens"},
        ],
        model="m",
        provider="p",
        decisions=[{"id": "d1", "decision": "JWT", "rationale": "Interoperable"}],
        topics=["auth", "jwt"],
    )
    return mcp_server.memory_commit(
        message="auth commit",
        summary="JWT refresh strategy chosen",
        model="m",
        provider="p",
        topics=["auth", "jwt"],
    )["sha"]


def test_mcp_new_tools_and_read_by_tag(tmp_path, monkeypatch):
    store = FileStore(root=tmp_path / "store")
    monkeypatch.setattr(mcp_server, "store", store)

    started = mcp_server.memory_start_session("Pizza Party")
    assert started["session"]["slug"] == "pizza-party"

    sessions = mcp_server.memory_list_sessions()
    assert any(s["name"] == "Pizza Party" for s in sessions["sessions"])

    current = mcp_server.memory_current_session()
    assert current["session"]["slug"] == "pizza-party"

    sha = _seed_with_tools()

    tag = mcp_server.memory_tag("stable", sha)
    assert tag["name"] == "stable"
    assert tag["sha"] == sha

    tags = mcp_server.memory_tags()
    assert tags["tags"][0]["name"] == "stable"

    read = mcp_server.memory_read("stable")
    assert read["sha"] == sha

    window = mcp_server.memory_read_window(last_n=1)
    assert window["end_index"] >= 1

    budget = mcp_server.memory_read_for_budget(max_chars=25)
    assert budget["used_chars"] >= 1

    search = mcp_server.memory_search("jwt", limit=3)
    assert len(search["hits"]) >= 1

    status = mcp_server.memory_compaction_status()
    assert status["status"] in {"idle", "running", "succeeded", "failed"}

    mcp_server.memory_start_session("Payments")
    switched = mcp_server.memory_use_session("pizza")
    assert switched["session"]["slug"] == "pizza-party"
