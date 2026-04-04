from typer.testing import CliRunner

from cli import main as cli_main
from memory import engine
from memory.store.file_store import FileStore


runner = CliRunner()


def _seed(store):
    engine.stage(
        store,
        messages=[
            {"role": "user", "content": "hello auth"},
            {"role": "assistant", "content": "jwt refresh details"},
        ],
        model="m",
        provider="p",
        topics=["auth", "jwt"],
    )
    return engine.commit(
        store,
        message="auth setup",
        summary="jwt refresh configured",
        model="m",
        provider="p",
        topics=["auth", "jwt"],
    ).sha


def test_cli_tag_and_show_by_tag(tmp_path, monkeypatch):
    store = FileStore(root=tmp_path / "store")
    monkeypatch.setattr(cli_main, "store", store)

    sha = _seed(store)

    tag_res = runner.invoke(cli_main.app, ["tag", "v1", sha])
    assert tag_res.exit_code == 0
    assert "v1" in tag_res.output

    tags_res = runner.invoke(cli_main.app, ["tags"])
    assert tags_res.exit_code == 0
    assert "v1" in tags_res.output

    show_res = runner.invoke(cli_main.app, ["show", "v1"])
    assert show_res.exit_code == 0
    assert sha in show_res.output


def test_cli_window_budget_search_and_compact_status(tmp_path, monkeypatch):
    store = FileStore(root=tmp_path / "store")
    monkeypatch.setattr(cli_main, "store", store)
    _seed(store)

    window_res = runner.invoke(cli_main.app, ["window", "--last-n", "1"])
    assert window_res.exit_code == 0
    assert "Range:" in window_res.output

    budget_res = runner.invoke(cli_main.app, ["budget", "--max-chars", "20"])
    assert budget_res.exit_code == 0
    assert "Used:" in budget_res.output

    search_res = runner.invoke(cli_main.app, ["search", "jwt", "--limit", "3"])
    assert search_res.exit_code == 0
    assert "auth setup" in search_res.output

    compact_status_res = runner.invoke(cli_main.app, ["compact-status"])
    assert compact_status_res.exit_code == 0
    assert "Status:" in compact_status_res.output


def test_cli_session_commands(tmp_path, monkeypatch):
    store = FileStore(root=tmp_path / "store")
    monkeypatch.setattr(cli_main, "store", store)

    new_res = runner.invoke(cli_main.app, ["session", "new", "Pizza Party"])
    assert new_res.exit_code == 0
    assert "pizza-party" in new_res.output

    list_res = runner.invoke(cli_main.app, ["session", "list"])
    assert list_res.exit_code == 0
    assert "Pizza Party" in list_res.output

    engine.start_session(store, "Payments")
    use_res = runner.invoke(cli_main.app, ["session", "use", "pizza"])
    assert use_res.exit_code == 0
    assert "pizza-party" in use_res.output

    current_res = runner.invoke(cli_main.app, ["session", "current"])
    assert current_res.exit_code == 0
    assert "pizza-party" in current_res.output
