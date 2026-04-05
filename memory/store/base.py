from __future__ import annotations

from abc import ABC, abstractmethod
from memory.models import Commit, CompactionState, SearchIndexData, Session, SessionNamespace


class BaseStore(ABC):

    # ── Session namespaces (conversation contexts) ────────────────────────────

    @abstractmethod
    def create_namespace(self, name: str) -> SessionNamespace: ...

    @abstractmethod
    def use_namespace(self, slug: str) -> SessionNamespace: ...

    @abstractmethod
    def current_namespace(self) -> SessionNamespace: ...

    @abstractmethod
    def list_namespaces(self) -> list[SessionNamespace]: ...

    # ── Commits (immutable) ───────────────────────────────────────────────────

    @abstractmethod
    def write_commit(self, commit: Commit) -> None: ...

    @abstractmethod
    def read_commit(self, sha: str) -> Commit: ...

    # ── Session (one globally active session for MVP) ─────────────────────────

    @abstractmethod
    def write_session(self, session: Session) -> None: ...

    @abstractmethod
    def read_session(self) -> Session | None: ...

    # ── Refs (branch → sha) ───────────────────────────────────────────────────

    @abstractmethod
    def write_ref(self, branch: str, sha: str) -> None: ...

    @abstractmethod
    def read_ref(self, branch: str) -> str | None: ...

    @abstractmethod
    def list_branches(self) -> list[str]: ...

    # ── Tags (name → sha) ─────────────────────────────────────────────────────

    @abstractmethod
    def write_tag(self, name: str, sha: str) -> None: ...

    @abstractmethod
    def read_tag(self, name: str) -> str | None: ...

    @abstractmethod
    def list_tags(self) -> list[str]: ...

    # ── HEAD ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def write_head(self, branch: str) -> None: ...

    @abstractmethod
    def read_head(self) -> str:
        """Returns current branch name."""
        ...

    # ── Compaction state ───────────────────────────────────────────────────────

    @abstractmethod
    def write_compaction_state(self, state: CompactionState) -> None: ...

    @abstractmethod
    def read_compaction_state(self) -> CompactionState: ...

    # ── Search index ───────────────────────────────────────────────────────────

    @abstractmethod
    def write_search_index(self, index: SearchIndexData) -> None: ...

    @abstractmethod
    def read_search_index(self) -> SearchIndexData: ...
