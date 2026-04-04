from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import platform
import shlex
import subprocess

from gui.adapters import CommandModelAdapter
from gui.config import ConfigStore, GuiConfig, LinkInfo, ModelCommand
from memory import engine
from memory.models import Message, Session
from memory.store.file_store import FileStore


class MemoryGuiService:
    def __init__(self, config_store: ConfigStore | None = None):
        self.config_store = config_store or ConfigStore()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load(self) -> tuple[GuiConfig, FileStore]:
        cfg = self.config_store.load()
        store_path = Path(cfg.memory_store_path).expanduser().resolve()
        store_path.mkdir(parents=True, exist_ok=True)
        store = FileStore(root=store_path)
        return cfg, store

    def _resolve_model_command(self, cfg: GuiConfig, model_id: str) -> ModelCommand:
        if model_id in cfg.model_commands:
            return cfg.model_commands[model_id]

        if model_id.startswith("ollama:"):
            model_name = model_id.split(":", 1)[1].strip()
            if not model_name:
                raise ValueError("Invalid ollama model id.")
            return ModelCommand(
                id=model_id,
                label=f"Ollama - {model_name}",
                command="ollama",
                args=["run", model_name, "{prompt}"],
                model_name=model_name,
                provider="ollama",
            )

        if model_id == "claude-desktop":
            return ModelCommand(
                id="claude-desktop",
                label="Claude Desktop",
                command="",
                args=[],
                model_name="claude-desktop",
                provider="anthropic",
            )

        raise ValueError(f"Unknown model id: {model_id}")

    def _list_ollama_model_ids(self) -> list[str]:
        try:
            completed = subprocess.run(
                ["ollama", "list"],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        if completed.returncode != 0:
            return []

        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            return []

        model_ids: list[str] = []
        for idx, line in enumerate(lines):
            if idx == 0 and "NAME" in line.upper():
                continue
            name = line.split()[0].strip()
            if ":" in name:
                model_ids.append(name)
        return model_ids

    def _launch_claude_desktop(self) -> None:
        cmd_env = os.getenv("CLAUDE_DESKTOP_LAUNCH_CMD", "").strip()
        candidates: list[list[str]] = []
        if cmd_env:
            candidates.append(shlex.split(cmd_env))

        # Common environments: Windows host, WSL, Linux desktop, macOS.
        candidates.extend(
            [
                ["cmd.exe", "/c", "start", "", "claude://"],
                ["xdg-open", "claude://"],
                ["open", "-a", "Claude"],
            ]
        )

        for cmd in candidates:
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except FileNotFoundError:
                continue

        raise RuntimeError(
            "Could not launch Claude Desktop automatically. "
            "Set CLAUDE_DESKTOP_LAUNCH_CMD to a working launch command."
        )

    @contextmanager
    def _session_scope(self, store: FileStore, slug: str | None):
        if not slug:
            yield
            return

        previous = store.current_namespace().slug
        if previous != slug:
            store.use_namespace(slug)
        try:
            yield
        finally:
            if previous != slug:
                store.use_namespace(previous)

    def _has_staged_data(self, session: Session | None) -> bool:
        if not session:
            return False
        return bool(
            session.staged_messages
            or session.staged_decisions
            or session.staged_open_questions
            or session.staged_artifacts
            or session.staged_topics
        )

    def _auto_commit_payload(self, store: FileStore, model_id: str) -> tuple[str, str]:
        status_obj = engine.status(store)
        session_obj = store.read_session()
        staged_messages = session_obj.staged_messages if session_obj else []

        commit_message = f"Auto-commit before unlink ({model_id})"

        prior_summary = (status_obj.summary or "").strip()
        latest_user = ""
        latest_assistant = ""
        for msg in reversed(staged_messages):
            if not latest_assistant and msg.role == "assistant":
                latest_assistant = msg.content.strip()
            if not latest_user and msg.role == "user":
                latest_user = msg.content.strip()
            if latest_user and latest_assistant:
                break

        snippets: list[str] = []
        if latest_user:
            snippets.append(f"Latest user request: {latest_user[:240]}")
        if latest_assistant:
            snippets.append(f"Latest assistant response: {latest_assistant[:240]}")

        summary_parts = [
            prior_summary or "Session context updated.",
            f"Auto-committed {len(staged_messages)} staged messages before model unlink.",
        ] + snippets
        summary = " ".join(part for part in summary_parts if part).strip()
        return commit_message, summary

    def _run_picker_command(self, cmd: list[str], timeout: int = 180) -> str | None:
        try:
            completed = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        if completed.returncode != 0:
            return None
        picked = (completed.stdout or "").strip()
        return picked or None

    def _is_wsl(self) -> bool:
        if os.getenv("WSL_DISTRO_NAME"):
            return True
        try:
            version = Path("/proc/version").read_text().lower()
        except OSError:
            return False
        return "microsoft" in version or "wsl" in version

    def _normalize_picked_path(self, picked: str) -> str:
        cleaned = picked.strip().strip('"').strip("'")
        if not cleaned:
            return cleaned

        # Convert Windows path to WSL path when running under WSL.
        if self._is_wsl() and len(cleaned) >= 2 and cleaned[1] == ":":
            try:
                completed = subprocess.run(
                    ["wslpath", "-u", cleaned],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    return completed.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return cleaned

    def _browse_directory(self) -> str | None:
        custom_picker = os.getenv("MEMORY_GUI_PICKER_CMD", "").strip()
        if custom_picker:
            picked = self._run_picker_command(shlex.split(custom_picker))
            if picked:
                return picked

        system = platform.system().lower()
        if system == "linux":
            # WSL often lacks zenity/kdialog; use Windows folder picker first.
            if self._is_wsl():
                for cmd in (
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Sta",
                        "-Command",
                        "$app = New-Object -ComObject Shell.Application; "
                        "$folder = $app.BrowseForFolder(0, 'Select llm-memory folder', 0, 0); "
                        "if ($folder) { Write-Output $folder.Self.Path }",
                    ],
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Sta",
                        "-Command",
                        "Add-Type -AssemblyName System.Windows.Forms; "
                        "$f=New-Object System.Windows.Forms.FolderBrowserDialog; "
                        "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }",
                    ],
                    [
                        "cmd.exe",
                        "/c",
                        "powershell -NoProfile -Sta -Command "
                        "\"Add-Type -AssemblyName System.Windows.Forms; "
                        "$f=New-Object System.Windows.Forms.FolderBrowserDialog; "
                        "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }\"",
                    ],
                ):
                    picked = self._run_picker_command(cmd)
                    if picked:
                        return self._normalize_picked_path(picked)

            for cmd in (
                ["zenity", "--file-selection", "--directory", "--title=Select memory store"],
                ["kdialog", "--getexistingdirectory", str(Path.home())],
            ):
                picked = self._run_picker_command(cmd)
                if picked:
                    return self._normalize_picked_path(picked)

        if system == "darwin":
            picked = self._run_picker_command(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Select llm-memory folder")',
                ]
            )
            if picked:
                return self._normalize_picked_path(picked)

        if system == "windows":
            picked = self._run_picker_command(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$f=New-Object System.Windows.Forms.FolderBrowserDialog; "
                    "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }",
                ]
            )
            if picked:
                return self._normalize_picked_path(picked)

        return None

    def _extract_json_payload(self, text: str) -> dict | None:
        cleaned = text.strip()
        if not cleaned:
            return None

        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            payload = json.loads(cleaned)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            payload = json.loads(cleaned[start : end + 1])
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None

    def _staged_message_excerpt(self, messages: list[Message], limit: int = 12) -> str:
        if not messages:
            return "(none)"
        excerpt = messages[-limit:]
        return "\n".join(f"{m.role.upper()}: {m.content[:800]}" for m in excerpt)

    def _generate_commit_payload(
        self,
        store: FileStore,
        model_cfg: ModelCommand | None,
        fallback_model_id: str,
    ) -> tuple[str, str]:
        fallback_message, fallback_summary = self._auto_commit_payload(
            store, fallback_model_id
        )
        if not model_cfg or not model_cfg.command:
            return fallback_message, fallback_summary

        status_obj = engine.status(store)
        session_obj = store.read_session()
        staged_messages = session_obj.staged_messages if session_obj else []
        if not staged_messages:
            return fallback_message, fallback_summary

        prompt = "\n".join(
            [
                "You write commit metadata for an LLM conversation memory system.",
                "Return only JSON with keys: message, summary.",
                "Rules:",
                "- message: concise title, max 72 chars, no trailing period.",
                "- summary: updated full session summary, 4-8 sentences, dense and factual.",
                "- No markdown, no backticks, no extra keys.",
                "",
                "Current summary:",
                status_obj.summary or "(empty)",
                "",
                "New staged transcript:",
                self._staged_message_excerpt(staged_messages, limit=16),
            ]
        )

        try:
            generated = CommandModelAdapter(model_cfg).generate(
                prompt=prompt, cwd=str(Path.cwd())
            )
            payload = self._extract_json_payload(generated)
            if not payload:
                return fallback_message, fallback_summary

            message = str(payload.get("message", "")).strip()
            summary = str(payload.get("summary", "")).strip()
            if not message or not summary:
                return fallback_message, fallback_summary
            if len(message) > 72:
                message = message[:72].rstrip()
            return message, summary
        except Exception:
            return fallback_message, fallback_summary

    def _session_payload(self, cfg: GuiConfig, session_obj) -> dict:
        link = cfg.session_links.get(session_obj.slug)
        return {
            "name": session_obj.name,
            "slug": session_obj.slug,
            "created_at": session_obj.created_at,
            "last_active_at": session_obj.last_active_at,
            "linked_model_id": link.model_id if link else None,
            "linked_at": link.linked_at if link else None,
        }

    def settings(self) -> dict:
        cfg, _ = self._load()
        return {
            "memory_store_path": cfg.memory_store_path,
            "models": self.available_models(cfg=cfg),
        }

    def available_models(self, cfg: GuiConfig | None = None) -> list[dict]:
        if cfg is None:
            cfg = self.config_store.load()

        models: list[dict] = []
        seen: set[str] = set()

        for model in cfg.model_commands.values():
            if model.id in seen:
                continue
            seen.add(model.id)
            models.append(
                {
                    "id": model.id,
                    "label": model.label,
                    "provider": model.provider,
                    "model_name": model.model_name,
                    "chat_capable": True,
                }
            )

        # Keep Claude visible in dropdown, even though chat runs in Claude Desktop.
        models.append(
            {
                "id": "claude-desktop",
                "label": "Claude Desktop",
                "provider": "anthropic",
                "model_name": "claude-desktop",
                "chat_capable": False,
            }
        )

        for ollama_name in self._list_ollama_model_ids():
            model_id = f"ollama:{ollama_name}"
            if model_id in seen:
                continue
            seen.add(model_id)
            models.append(
                {
                    "id": model_id,
                    "label": f"Ollama - {ollama_name}",
                    "provider": "ollama",
                    "model_name": ollama_name,
                    "chat_capable": True,
                }
            )

        return models

    def update_memory_store_path(self, memory_store_path: str) -> dict:
        path = Path(memory_store_path).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)

        cfg = self.config_store.load()
        cfg.memory_store_path = str(path)
        # Links are store-specific; clear old links when switching stores.
        cfg.session_links = {}
        self.config_store.save(cfg)
        return self.settings()

    def browse_memory_store_path(self) -> dict:
        picked = self._browse_directory()
        if not picked:
            raise ValueError(
                "No directory selected. On WSL, ensure Windows interop is enabled "
                "(powershell.exe available), or set MEMORY_GUI_PICKER_CMD."
            )
        return self.update_memory_store_path(picked)

    def list_sessions(self) -> dict:
        cfg, store = self._load()
        result = engine.list_sessions(store)
        return {
            "current_slug": result.current_slug,
            "sessions": [self._session_payload(cfg, s) for s in result.sessions],
        }

    def current_session(self) -> dict:
        cfg, store = self._load()
        current = engine.current_session(store)
        return self._session_payload(cfg, current.session)

    def start_session(self, name: str) -> dict:
        cfg, store = self._load()
        result = engine.start_session(store, name)
        cfg.session_links.pop(result.session.slug, None)
        self.config_store.save(cfg)
        return self._session_payload(cfg, result.session)

    def use_session(self, query: str) -> dict:
        cfg, store = self._load()
        result = engine.use_session(store, query)
        payload = self._session_payload(cfg, result.session)
        payload["matched_query"] = result.matched_query
        payload["fuzzy_match"] = result.fuzzy_match
        return payload

    def link_model(self, session_slug: str, model_id: str) -> dict:
        cfg, store = self._load()
        # Validate model id (supports dynamic ollama:* and Claude Desktop virtual adapter).
        self._resolve_model_command(cfg, model_id)

        with self._session_scope(store, session_slug):
            current = store.current_namespace()
            existing = cfg.session_links.get(current.slug)
            if existing and existing.model_id != model_id:
                raise ValueError(
                    f"Session '{current.name}' is already linked to {existing.model_id}. "
                    "Unlink first."
                )

            cfg.session_links[current.slug] = LinkInfo(
                model_id=model_id,
                linked_at=self._now_iso(),
            )
            self.config_store.save(cfg)
            if model_id == "claude-desktop":
                self._launch_claude_desktop()
            return self._session_payload(cfg, current)

    def unlink_model(
        self,
        session_slug: str,
        action: str | None = None,
        commit_message: str | None = None,
        summary: str | None = None,
    ) -> dict:
        cfg, store = self._load()

        with self._session_scope(store, session_slug):
            current = store.current_namespace()
            link = cfg.session_links.get(current.slug)
            if not link:
                return self._session_payload(cfg, current)

            session_obj = store.read_session()
            if self._has_staged_data(session_obj):
                # Default behavior: auto-commit staged memory before unlink.
                requested_action = action or "commit"
                if requested_action == "discard":
                    session_obj.staged_messages = []
                    session_obj.staged_decisions = []
                    session_obj.staged_open_questions = []
                    session_obj.staged_artifacts = []
                    session_obj.staged_topics = []
                    store.write_session(session_obj)
                else:
                    if not commit_message or not summary:
                        model_cfg = self._resolve_model_command(cfg, link.model_id)
                        auto_message, auto_summary = self._generate_commit_payload(
                            store=store,
                            model_cfg=model_cfg,
                            fallback_model_id=link.model_id,
                        )
                        commit_message = commit_message or auto_message
                        summary = summary or auto_summary
                    model_cfg = self._resolve_model_command(cfg, link.model_id)
                    engine.commit(
                        store,
                        message=commit_message,
                        summary=summary,
                        model=model_cfg.model_name,
                        provider=model_cfg.provider,
                    )

            del cfg.session_links[current.slug]
            self.config_store.save(cfg)
            return self._session_payload(cfg, current)

    def status(self, session_slug: str | None = None) -> dict:
        cfg, store = self._load()
        with self._session_scope(store, session_slug):
            s = engine.status(store).model_dump()
            current = store.current_namespace()
            link = cfg.session_links.get(current.slug)
            staged = store.read_session()
            s["linked_model_id"] = link.model_id if link else None
            s["has_staged_data"] = self._has_staged_data(staged)
            s["staged_message_count"] = len(staged.staged_messages) if staged else 0
            return s

    def list_commits(self, limit: int = 50, session_slug: str | None = None) -> list[dict]:
        _, store = self._load()
        with self._session_scope(store, session_slug):
            entries = engine.log_commits(store, limit=limit)
            out = []
            for entry in entries:
                commit_obj = engine.read_commit(store, entry.sha)
                out.append(
                    {
                        "sha": entry.sha,
                        "message": entry.message,
                        "author_model": entry.author_model,
                        "timestamp": entry.timestamp,
                        "summary": commit_obj.tree.context.summary,
                        "message_count": len(commit_obj.tree.messages),
                        "topics": commit_obj.tree.context.topics,
                    }
                )
            return out

    def get_commit(self, sha: str, session_slug: str | None = None) -> dict:
        _, store = self._load()
        with self._session_scope(store, session_slug):
            return engine.read_commit(store, sha).model_dump()

    def _format_messages(self, messages: list[Message]) -> str:
        return "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)

    def _build_prompt(self, store: FileStore, user_message: str) -> str:
        lines: list[str] = [
            "You are continuing an existing conversation.",
            "Use the conversation context below and answer the latest user message.",
        ]

        try:
            budget = engine.read_for_budget(store, max_chars=6000)
            if budget.messages:
                lines.append("\nCommitted conversation context:")
                lines.append(self._format_messages(budget.messages))
        except ValueError:
            pass

        staged = store.read_session()
        if staged and staged.staged_messages:
            lines.append("\nRecently staged (not yet committed):")
            lines.append(self._format_messages(staged.staged_messages))

        lines.append("\nLatest user message:")
        lines.append(user_message)
        lines.append("\nRespond with just the assistant reply.")
        return "\n".join(lines)

    def send_chat(self, user_message: str, session_slug: str | None = None) -> dict:
        if not user_message.strip():
            raise ValueError("user_message cannot be empty")

        cfg, store = self._load()
        with self._session_scope(store, session_slug):
            current = store.current_namespace()
            link = cfg.session_links.get(current.slug)
            if not link:
                raise ValueError(
                    f"Session '{current.name}' is not linked. Link a model before chatting."
                )

            model_cfg = cfg.model_commands.get(link.model_id)
            if not model_cfg:
                model_cfg = self._resolve_model_command(cfg, link.model_id)
            if model_cfg.id == "claude-desktop":
                raise ValueError(
                    "Claude Desktop is linked for this session. Chat in Claude Desktop; "
                    "GUI direct send is for CLI/local adapters."
                )

            if not model_cfg:
                raise ValueError(
                    f"Linked model '{link.model_id}' is not configured in GUI settings."
                )

            if link.model_id == "gemini-cli" and not os.getenv("GEMINI_API_KEY", "").strip():
                raise ValueError(
                    "GEMINI_API_KEY is required for gemini-cli. "
                    "Set it in your .env and restart memory-gui."
                )

            prompt = self._build_prompt(store, user_message)
            adapter = CommandModelAdapter(model_cfg)
            assistant_reply = adapter.generate(prompt=prompt, cwd=str(Path.cwd()))

            stage_result = engine.stage(
                store,
                messages=[
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_reply},
                ],
                model=model_cfg.model_name,
                provider=model_cfg.provider,
            )

            return {
                "assistant_message": assistant_reply,
                "staged_messages": stage_result.staged_messages,
                "session_slug": current.slug,
                "model_id": link.model_id,
            }

    def commit_staged(
        self,
        message: str | None = None,
        summary: str | None = None,
        session_slug: str | None = None,
        topics: list[str] | None = None,
    ) -> dict:
        cfg, store = self._load()
        with self._session_scope(store, session_slug):
            current = store.current_namespace()
            link = cfg.session_links.get(current.slug)
            model_cfg: ModelCommand | None = None
            if link:
                model_cfg = self._resolve_model_command(cfg, link.model_id)
                model_name = model_cfg.model_name
                provider = model_cfg.provider
                fallback_model_id = link.model_id
            else:
                session_obj = store.read_session()
                model_name = session_obj.active_model if session_obj else "unknown"
                provider = session_obj.active_provider if session_obj else "unknown"
                fallback_model_id = model_name

            final_message = (message or "").strip()
            final_summary = (summary or "").strip()
            if not final_message or not final_summary:
                generated_message, generated_summary = self._generate_commit_payload(
                    store=store,
                    model_cfg=model_cfg,
                    fallback_model_id=fallback_model_id,
                )
                final_message = final_message or generated_message
                final_summary = final_summary or generated_summary

            if not final_message:
                raise ValueError("message cannot be empty")
            if not final_summary:
                raise ValueError("summary cannot be empty")

            result = engine.commit(
                store,
                message=final_message,
                summary=final_summary,
                model=model_name,
                provider=provider,
                topics=topics,
            )
            return result.model_dump()

    def discard_staged(self, session_slug: str | None = None) -> dict:
        _, store = self._load()
        with self._session_scope(store, session_slug):
            session_obj = store.read_session()
            if not session_obj:
                return {"discarded": 0}

            discarded = len(session_obj.staged_messages)
            session_obj.staged_messages = []
            session_obj.staged_decisions = []
            session_obj.staged_open_questions = []
            session_obj.staged_artifacts = []
            session_obj.staged_topics = []
            store.write_session(session_obj)
            return {"discarded": discarded}
