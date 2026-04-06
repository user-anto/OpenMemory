from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from memory.models import Commit, CompactionState, SearchIndexData, Session, SessionNamespace
from memory.store.base import BaseStore

_ACTIVE_SESSION_FILE = "active_session.json"
_COMPACTION_STATE_FILE = "compaction_state.json"
_SEARCH_INDEX_FILE = "search_index.json"
_HEAD_FILE = "HEAD"
_CURRENT_NAMESPACE_FILE = "CURRENT_SESSION"
_NAMESPACE_INDEX_FILE = "index.json"
_DEFAULT_NAMESPACE_NAME = "Default"
_DEFAULT_NAMESPACE_SLUG = "default"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class FileStore(BaseStore):
    def __init__(self, root: Path | None = None):
        raw = os.getenv("MEMORY_STORE_PATH", "~/.llm-memory")
        self.root = (root or Path(raw)).expanduser().resolve()
        self._namespace_slug: str | None = None
        self._ensure_layout()

    # ── Global path helpers ────────────────────────────────────────────────────

    def _commits_root(self) -> Path:
        return self.root / "commits"

    def _sessions_root(self) -> Path:
        return self.root / "sessions"

    def _refs_heads_root(self) -> Path:
        return self.root / "refs" / "heads"

    def _refs_tags_root(self) -> Path:
        return self.root / "refs" / "tags"

    def _namespace_index_path(self) -> Path:
        return self._sessions_root() / _NAMESPACE_INDEX_FILE

    def _current_namespace_path(self) -> Path:
        return self.root / _CURRENT_NAMESPACE_FILE

    # ── Active namespace path helpers ──────────────────────────────────────────

    def _active_slug(self) -> str:
        if self._namespace_slug:
            return self._namespace_slug
        path = self._current_namespace_path()
        if path.exists():
            self._namespace_slug = path.read_text().strip()
        else:
            self._namespace_slug = _DEFAULT_NAMESPACE_SLUG
        return self._namespace_slug

    def _namespace_dir(self, slug: str) -> Path:
        return self._sessions_root() / slug

    def _namespace_commits_dir(self, slug: str | None = None) -> Path:
        return self._commits_root() / (slug or self._active_slug())

    def _namespace_refs_dir(self, slug: str | None = None) -> Path:
        return self._refs_heads_root() / (slug or self._active_slug())

    def _namespace_tags_dir(self, slug: str | None = None) -> Path:
        return self._refs_tags_root() / (slug or self._active_slug())

    def _namespace_head_file(self, slug: str | None = None) -> Path:
        return self._namespace_dir(slug or self._active_slug()) / _HEAD_FILE

    def _namespace_session_file(self, slug: str | None = None) -> Path:
        return self._namespace_dir(slug or self._active_slug()) / _ACTIVE_SESSION_FILE

    def _namespace_compaction_state_file(self, slug: str | None = None) -> Path:
        return self._namespace_dir(slug or self._active_slug()) / _COMPACTION_STATE_FILE

    def _namespace_search_index_file(self, slug: str | None = None) -> Path:
        return self._namespace_dir(slug or self._active_slug()) / _SEARCH_INDEX_FILE

    # ── Namespace internals ────────────────────────────────────────────────────

    def _slugify(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "session"

    def _read_namespace_index(self) -> list[SessionNamespace]:
        path = self._namespace_index_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        sessions = data.get("sessions", [])
        return [SessionNamespace.model_validate(item) for item in sessions]

    def _write_namespace_index(self, sessions: list[SessionNamespace]) -> None:
        payload = {"sessions": [item.model_dump(mode="json") for item in sessions]}
        self._namespace_index_path().write_text(json.dumps(payload, indent=2))

    def _ensure_namespace_layout(self, slug: str) -> None:
        for d in (
            self._namespace_dir(slug),
            self._namespace_commits_dir(slug),
            self._namespace_refs_dir(slug),
            self._namespace_tags_dir(slug),
        ):
            d.mkdir(parents=True, exist_ok=True)
        head = self._namespace_head_file(slug)
        if not head.exists():
            head.write_text("main")

    def _upsert_namespace(self, session_ns: SessionNamespace) -> SessionNamespace:
        sessions = self._read_namespace_index()
        replaced = False
        for i, existing in enumerate(sessions):
            if existing.slug == session_ns.slug:
                sessions[i] = session_ns
                replaced = True
                break
        if not replaced:
            sessions.append(session_ns)
        self._write_namespace_index(sessions)
        return session_ns

    def _write_text_atomic(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content)
        tmp_path.replace(path)

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _ensure_layout(self) -> None:
        for d in (
            self._commits_root(),
            self._sessions_root(),
            self._refs_heads_root(),
            self._refs_tags_root(),
        ):
            d.mkdir(parents=True, exist_ok=True)

        sessions = self._read_namespace_index()
        if not sessions:
            now = _now_utc()
            sessions = [
                SessionNamespace(
                    name=_DEFAULT_NAMESPACE_NAME,
                    slug=_DEFAULT_NAMESPACE_SLUG,
                    created_at=now,
                    last_active_at=now,
                )
            ]
            self._write_namespace_index(sessions)
            self._ensure_namespace_layout(_DEFAULT_NAMESPACE_SLUG)

        current_path = self._current_namespace_path()
        if not current_path.exists():
            current_path.write_text(sessions[0].slug)
        self._namespace_slug = current_path.read_text().strip()
        self._ensure_namespace_layout(self._namespace_slug)

    # ── Session namespaces ─────────────────────────────────────────────────────

    def create_namespace(self, name: str) -> SessionNamespace:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Session name cannot be empty.")

        sessions = self._read_namespace_index()
        lower_names = {s.name.lower() for s in sessions}
        if cleaned.lower() in lower_names:
            raise ValueError(f"Session name already exists: {cleaned}")

        slug_base = self._slugify(cleaned)
        slug = slug_base
        taken = {s.slug for s in sessions}
        idx = 2
        while slug in taken:
            slug = f"{slug_base}-{idx}"
            idx += 1

        now = _now_utc()
        session_ns = SessionNamespace(
            name=cleaned,
            slug=slug,
            created_at=now,
            last_active_at=now,
        )
        self._ensure_namespace_layout(slug)
        sessions.append(session_ns)
        self._write_namespace_index(sessions)
        return self.use_namespace(slug)

    def use_namespace(self, slug: str) -> SessionNamespace:
        sessions = self._read_namespace_index()
        for i, existing in enumerate(sessions):
            if existing.slug == slug:
                updated = existing.model_copy(update={"last_active_at": _now_utc()})
                sessions[i] = updated
                self._write_namespace_index(sessions)
                self._namespace_slug = slug
                self._current_namespace_path().write_text(slug)
                self._ensure_namespace_layout(slug)
                return updated
        raise KeyError(f"Unknown session namespace: {slug}")

    def current_namespace(self) -> SessionNamespace:
        slug = self._active_slug()
        sessions = self._read_namespace_index()
        for existing in sessions:
            if existing.slug == slug:
                return existing
        raise KeyError(f"Current session namespace not found: {slug}")

    def list_namespaces(self) -> list[SessionNamespace]:
        return sorted(self._read_namespace_index(), key=lambda s: s.created_at)

    # ── Commits ────────────────────────────────────────────────────────────────

    def write_commit(self, commit: Commit) -> None:
        path = self._namespace_commits_dir() / f"{commit.sha}.json"
        self._write_text_atomic(path, commit.model_dump_json(indent=2))

    def read_commit(self, sha: str) -> Commit:
        path = self._namespace_commits_dir() / f"{sha}.json"
        if not path.exists():
            raise KeyError(f"Commit not found: {sha}")
        return Commit.model_validate_json(path.read_text())

    # ── Session ────────────────────────────────────────────────────────────────

    def write_session(self, session: Session) -> None:
        self._write_text_atomic(self._namespace_session_file(), session.model_dump_json(indent=2))

    def read_session(self) -> Session | None:
        path = self._namespace_session_file()
        if not path.exists():
            return None
        data = json.loads(path.read_text())

        # Backward-compat for old staging schema.
        updates = data.pop("staged_context_updates", None) or {}
        if updates:
            data["staged_decisions"] = (
                data.get("staged_decisions", []) + updates.get("decisions", [])
            )
            data["staged_open_questions"] = (
                data.get("staged_open_questions", []) + updates.get("open_questions", [])
            )
            data["staged_artifacts"] = (
                data.get("staged_artifacts", []) + updates.get("artifacts", [])
            )
            data["staged_topics"] = data.get("staged_topics", []) + updates.get("topics", [])

        return Session.model_validate(data)

    # ── Refs ───────────────────────────────────────────────────────────────────

    def write_ref(self, branch: str, sha: str) -> None:
        self._write_text_atomic(self._namespace_refs_dir() / branch, sha)

    def read_ref(self, branch: str) -> str | None:
        path = self._namespace_refs_dir() / branch
        return path.read_text().strip() if path.exists() else None

    def list_branches(self) -> list[str]:
        refs_dir = self._namespace_refs_dir()
        if not refs_dir.exists():
            return []
        return [p.name for p in refs_dir.iterdir() if p.is_file()]

    # ── Tags ───────────────────────────────────────────────────────────────────

    def write_tag(self, name: str, sha: str) -> None:
        self._write_text_atomic(self._namespace_tags_dir() / name, sha)

    def read_tag(self, name: str) -> str | None:
        path = self._namespace_tags_dir() / name
        return path.read_text().strip() if path.exists() else None

    def list_tags(self) -> list[str]:
        tags_dir = self._namespace_tags_dir()
        if not tags_dir.exists():
            return []
        return [p.name for p in tags_dir.iterdir() if p.is_file()]

    # ── HEAD ───────────────────────────────────────────────────────────────────

    def write_head(self, branch: str) -> None:
        self._write_text_atomic(self._namespace_head_file(), branch)

    def read_head(self) -> str:
        return self._namespace_head_file().read_text().strip()

    # ── Compaction state ───────────────────────────────────────────────────────

    def write_compaction_state(self, state: CompactionState) -> None:
        self._write_text_atomic(
            self._namespace_compaction_state_file(),
            state.model_dump_json(indent=2),
        )

    def read_compaction_state(self) -> CompactionState:
        path = self._namespace_compaction_state_file()
        if not path.exists():
            return CompactionState()
        raw = path.read_text().strip()
        if not raw:
            return CompactionState()
        try:
            return CompactionState.model_validate_json(raw)
        except Exception:
            return CompactionState()

    # ── Search index ───────────────────────────────────────────────────────────

    def write_search_index(self, index: SearchIndexData) -> None:
        self._write_text_atomic(
            self._namespace_search_index_file(),
            index.model_dump_json(indent=2),
        )

    def read_search_index(self) -> SearchIndexData:
        path = self._namespace_search_index_file()
        if not path.exists():
            return SearchIndexData()
        raw = path.read_text().strip()
        if not raw:
            return SearchIndexData()
        try:
            return SearchIndexData.model_validate_json(raw)
        except Exception:
            return SearchIndexData()
