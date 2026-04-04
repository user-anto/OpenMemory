import pytest
import subprocess
from pathlib import Path

from gui.config import ConfigStore, ModelCommand
from gui.service import MemoryGuiService


def _service(tmp_path):
    cfg_store = ConfigStore(path=tmp_path / "gui-config.json")
    cfg = cfg_store.load()
    cfg.memory_store_path = str((tmp_path / "memory-store").resolve())
    cfg.model_commands["echo-model"] = ModelCommand(
        id="echo-model",
        label="Echo Model",
        command="/bin/echo",
        args=["{prompt}"],
        model_name="echo-model",
        provider="local",
    )
    cfg_store.save(cfg)
    return MemoryGuiService(config_store=cfg_store)


def test_gui_unlink_auto_commits_by_default(tmp_path):
    service = _service(tmp_path)
    session = service.start_session("Pizza Party")
    slug = session["slug"]

    service.link_model(slug, "echo-model")
    service.send_chat("hello", session_slug=slug)

    out = service.unlink_model(slug)
    assert out["linked_model_id"] is None

    status = service.status(session_slug=slug)
    assert status["staged_message_count"] == 0
    commits = service.list_commits(session_slug=slug)
    assert len(commits) == 1
    assert commits[0]["message"].startswith("Auto-commit before unlink")


def test_gui_unlink_with_commit_creates_commit(tmp_path):
    service = _service(tmp_path)
    slug = service.start_session("French Revolution Chat")["slug"]

    service.link_model(slug, "echo-model")
    service.send_chat("What happened in 1789?", session_slug=slug)

    out = service.unlink_model(
        slug,
        action="commit",
        commit_message="Auto commit before unlink",
        summary="Saved staged chat before unlinking model",
    )
    assert out["linked_model_id"] is None

    commits = service.list_commits(session_slug=slug)
    assert len(commits) == 1
    assert commits[0]["message"] == "Auto commit before unlink"


def test_available_models_includes_ollama_and_claude(tmp_path, monkeypatch):
    service = _service(tmp_path)

    class _Done:
        returncode = 0
        stdout = (
            "NAME ID SIZE MODIFIED\n"
            "llama3.2:latest abc 2 GB now\n"
            "qwen2.5:7b def 4 GB now\n"
        )
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _Done())
    models = service.available_models()
    ids = {m["id"] for m in models}
    assert "claude-desktop" in ids
    assert "ollama:llama3.2:latest" in ids
    assert "ollama:qwen2.5:7b" in ids


def test_linking_claude_launches_desktop(tmp_path, monkeypatch):
    service = _service(tmp_path)
    slug = service.start_session("Claude Session")["slug"]

    launched = {"called": False}

    def _fake_launch():
        launched["called"] = True

    monkeypatch.setattr(service, "_launch_claude_desktop", _fake_launch)
    service.link_model(slug, "claude-desktop")
    assert launched["called"] is True


def test_commit_staged_auto_generates_metadata_with_model(tmp_path, monkeypatch):
    service = _service(tmp_path)
    slug = service.start_session("Auto Commit Session")["slug"]
    service.link_model(slug, "echo-model")
    service.send_chat("Summarize this context", session_slug=slug)

    monkeypatch.setattr(
        "gui.adapters.CommandModelAdapter.generate",
        lambda self, prompt, cwd=None: (
            '{"message":"Auto memory checkpoint","summary":"Conversation summary updated."}'
        ),
    )

    out = service.commit_staged(session_slug=slug)
    commit = service.get_commit(out["sha"], session_slug=slug)
    assert commit["message"] == "Auto memory checkpoint"
    assert commit["tree"]["context"]["summary"] == "Conversation summary updated."


def test_browse_memory_store_path_updates_config(tmp_path, monkeypatch):
    service = _service(tmp_path)
    picked = (tmp_path / "picked-memory").resolve()
    monkeypatch.setattr(service, "_browse_directory", lambda: str(picked))

    settings = service.browse_memory_store_path()
    assert Path(settings["memory_store_path"]) == picked


def test_update_memory_store_path_clears_session_links(tmp_path):
    service = _service(tmp_path)
    slug = service.start_session("Linked Session")["slug"]
    service.link_model(slug, "echo-model")
    assert service.status(session_slug=slug)["linked_model_id"] == "echo-model"

    new_store = tmp_path / "another-store"
    service.update_memory_store_path(str(new_store))
    current = service.current_session()
    assert current["linked_model_id"] is None


def test_wsl_path_is_normalized_when_picked(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_is_wsl", lambda: True)

    class _Done:
        returncode = 0
        stdout = "/mnt/c/Users/ranta/memory\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _Done())
    normalized = service._normalize_picked_path(r"C:\Users\ranta\memory")
    assert normalized == "/mnt/c/Users/ranta/memory"


def test_gemini_cli_requires_api_key(tmp_path, monkeypatch):
    service = _service(tmp_path)
    slug = service.start_session("Gemini Session")["slug"]
    service.link_model(slug, "gemini-cli")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        service.send_chat("hello", session_slug=slug)


def test_config_enforces_gemini_flash_args(tmp_path):
    cfg_store = ConfigStore(path=tmp_path / "gui-config.json")
    cfg = cfg_store.load()
    gemini = cfg.model_commands["gemini-cli"]
    assert gemini.args == ["-m", "gemini-2.5-flash", "-p", "{prompt}"]
    assert gemini.model_name == "gemini-2.5-flash"
