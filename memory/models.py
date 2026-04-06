from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field


# ── Leaf types ────────────────────────────────────────────────────────────────

class Decision(BaseModel):
    id: str
    decision: str
    rationale: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(BaseModel):
    type: Literal["file", "url", "snippet", "other"]
    path: str
    description: str


class Message(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Populated only on assistant messages
    model: str | None = None
    provider: str | None = None


# ── Context: semantic layer of a commit ───────────────────────────────────────

class Context(BaseModel):
    summary: str = ""
    decisions: list[Decision] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


# ── CommitMetadata ────────────────────────────────────────────────────────────

class CommitMetadata(BaseModel):
    token_count: int = 0
    compacted: bool = False
    # Index into messages[] up to which compaction has been applied
    # NOTE: compaction is the one deliberate mutating exception to
    # otherwise immutable commit objects.
    compacted_up_to_index: int | None = None


# ── CommitTree: everything stored in a commit ─────────────────────────────────

class CommitTree(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    context: Context = Field(default_factory=Context)
    metadata: CommitMetadata = Field(default_factory=CommitMetadata)


# ── Author ────────────────────────────────────────────────────────────────────

class Author(BaseModel):
    model: str
    provider: str
    session_id: str


# ── Commit: the immutable unit of memory ─────────────────────────────────────

class Commit(BaseModel):
    sha: str
    parent: str | None = None          # None only for the root commit
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str
    author: Author
    tree: CommitTree = Field(default_factory=CommitTree)


# ── Session: the mutable staging area ────────────────────────────────────────

class Session(BaseModel):
    session_id: str
    branch: str = "main"
    head_commit: str | None = None     # SHA this session branched from
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    active_model: str = "unknown"
    active_provider: str = "unknown"
    staged_messages: list[Message] = Field(default_factory=list)
    staged_decisions: list[Decision] = Field(default_factory=list)
    staged_open_questions: list[str] = Field(default_factory=list)
    staged_artifacts: list[Artifact] = Field(default_factory=list)
    staged_topics: list[str] = Field(default_factory=list)


# ── Lightweight types returned by tools to LLMs ───────────────────────────────

class StatusResult(BaseModel):
    branch: str
    head: str | None
    message: str | None
    author_model: str | None
    session_name: str | None = None
    session_slug: str | None = None
    summary: str
    open_questions: list[str]
    topics: list[str]


class LogEntry(BaseModel):
    sha: str
    message: str
    author_model: str
    timestamp: datetime


class CommitResult(BaseModel):
    sha: str
    branch: str
    message: str


class StageResult(BaseModel):
    staged_messages: int
    session_id: str


class CompactResult(BaseModel):
    status: str
    sha: str | None = None
    compacted_message_count: int | None = None
    remaining_message_count: int | None = None
    message: str | None = None


class TagResult(BaseModel):
    name: str
    sha: str


class TagListResult(BaseModel):
    tags: list[TagResult] = Field(default_factory=list)


class ReadWindowResult(BaseModel):
    sha: str
    start_index: int
    end_index: int
    total_messages: int
    messages: list[Message] = Field(default_factory=list)


class ReadBudgetResult(BaseModel):
    sha: str
    max_chars: int
    used_chars: int
    start_index: int
    end_index: int
    total_messages: int
    messages: list[Message] = Field(default_factory=list)


class SearchHit(BaseModel):
    sha: str
    score: float
    message: str
    summary: str
    topics: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)


# ── Search index (persistent inverted index) ─────────────────────────────────

class IndexDocument(BaseModel):
    """Lightweight commit metadata stored in the search index."""
    message: str
    summary: str
    topics: list[str] = Field(default_factory=list)
    timestamp: datetime


class SearchIndexData(BaseModel):
    """Persistent search index: inverted postings + forward document table."""
    version: int = 1
    head_sha: str | None = None
    documents: dict[str, IndexDocument] = Field(default_factory=dict)
    postings: dict[str, dict[str, float]] = Field(default_factory=dict)


class CompactionState(BaseModel):
    status: Literal["idle", "running", "succeeded", "failed"] = "idle"
    sha: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class CompactionStatusResult(BaseModel):
    status: Literal["idle", "running", "succeeded", "failed"]
    sha: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class SessionNamespace(BaseModel):
    name: str
    slug: str
    created_at: datetime
    last_active_at: datetime


class SessionNamespaceResult(BaseModel):
    session: SessionNamespace
    matched_query: str | None = None
    fuzzy_match: bool = False


class SessionNamespaceListResult(BaseModel):
    current_slug: str
    sessions: list[SessionNamespace] = Field(default_factory=list)
