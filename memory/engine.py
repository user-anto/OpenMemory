"""
engine.py — pure business logic.

Every public function takes a store and explicit arguments.
No global state. No framework imports.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone

import httpx

from memory.hashing import sha_for_commit
from memory.models import (
    Artifact,
    Author,
    Commit,
    CommitMetadata,
    CommitResult,
    CommitTree,
    CompactResult,
    CompactionState,
    CompactionStatusResult,
    Context,
    Decision,
    LogEntry,
    Message,
    ReadBudgetResult,
    ReadWindowResult,
    SearchHit,
    SearchResult,
    Session,
    SessionNamespaceListResult,
    SessionNamespaceResult,
    StageResult,
    StatusResult,
    TagListResult,
    TagResult,
)
from memory.store.base import BaseStore

log = logging.getLogger(__name__)

COMPACT_KEEP_LAST = 20
_TAG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SEARCH_TERM_RE = re.compile(r"[a-z0-9]+")
_SESSION_TERM_RE = re.compile(r"[a-z0-9]+")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _head_sha(store: BaseStore) -> str | None:
    branch = store.read_head()
    return store.read_ref(branch)


def _get_or_create_session(store: BaseStore) -> Session:
    session = store.read_session()
    if session:
        return session
    branch = store.read_head()
    session = Session(
        session_id=f"sess_{uuid.uuid4().hex[:8]}",
        branch=branch,
        head_commit=store.read_ref(branch),
    )
    store.write_session(session)
    return session


def _validate_tag_name(name: str) -> str:
    cleaned = name.strip()
    if not _TAG_NAME_RE.fullmatch(cleaned):
        raise ValueError(
            "Invalid tag name. Use 1-128 characters: letters, numbers, ., _, -"
        )
    return cleaned


def _namespace_result(
    *,
    session,
    matched_query: str | None = None,
    fuzzy_match: bool = False,
) -> SessionNamespaceResult:
    return SessionNamespaceResult(
        session=session,
        matched_query=matched_query,
        fuzzy_match=fuzzy_match,
    )


def start_session(store: BaseStore, name: str) -> SessionNamespaceResult:
    session_ns = store.create_namespace(name)
    return _namespace_result(session=session_ns)


def current_session(store: BaseStore) -> SessionNamespaceResult:
    session_ns = store.current_namespace()
    return _namespace_result(session=session_ns)


def list_sessions(store: BaseStore) -> SessionNamespaceListResult:
    current = store.current_namespace()
    return SessionNamespaceListResult(
        current_slug=current.slug,
        sessions=store.list_namespaces(),
    )


def use_session(store: BaseStore, name_or_slug: str) -> SessionNamespaceResult:
    query = name_or_slug.strip()
    if not query:
        raise ValueError("Session query cannot be empty.")

    sessions = store.list_namespaces()
    if not sessions:
        raise KeyError("No sessions found.")

    lower = query.lower()
    for s in sessions:
        if s.slug == lower or s.name.lower() == lower:
            return _namespace_result(
                session=store.use_namespace(s.slug),
                matched_query=query,
                fuzzy_match=False,
            )

    query_terms = set(_SESSION_TERM_RE.findall(lower))
    best_score = 0.0
    best = None
    for s in sessions:
        for candidate in (s.name.lower(), s.slug):
            if lower in candidate:
                score = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, lower, candidate).ratio()
                cand_terms = set(_SESSION_TERM_RE.findall(candidate))
                overlap = (
                    len(query_terms & cand_terms) / len(query_terms)
                    if query_terms
                    else 0.0
                )
                score = max(ratio, overlap)
            if score > best_score:
                best_score = score
                best = s

    if not best or best_score < 0.5:
        raise KeyError(f"No matching session found for query: {query}")

    return _namespace_result(
        session=store.use_namespace(best.slug),
        matched_query=query,
        fuzzy_match=True,
    )


def resolve_ref(store: BaseStore, sha_or_tag: str | None = None) -> str | None:
    """Resolve a commit reference. Accepts sha, tag, or None (HEAD)."""
    if sha_or_tag is None:
        return _head_sha(store)

    try:
        store.read_commit(sha_or_tag)
        return sha_or_tag
    except KeyError:
        tag_sha = store.read_tag(sha_or_tag)
        if not tag_sha:
            raise KeyError(f"Unknown reference: {sha_or_tag}") from None
        # Validate that the tag points to an existing commit.
        store.read_commit(tag_sha)
        return tag_sha


def _resolve_commit(store: BaseStore, sha_or_tag: str | None = None) -> Commit:
    resolved = resolve_ref(store, sha_or_tag)
    if not resolved:
        raise ValueError("No commits yet.")
    return store.read_commit(resolved)


def status(store: BaseStore) -> StatusResult:
    current_ns = store.current_namespace()
    branch = store.read_head()
    sha = store.read_ref(branch)
    if not sha:
        return StatusResult(
            branch=branch,
            head=None,
            message=None,
            author_model=None,
            session_name=current_ns.name,
            session_slug=current_ns.slug,
            summary="No commits yet.",
            open_questions=[],
            topics=[],
        )
    commit_obj = store.read_commit(sha)
    return StatusResult(
        branch=branch,
        head=sha,
        message=commit_obj.message,
        author_model=commit_obj.author.model,
        session_name=current_ns.name,
        session_slug=current_ns.slug,
        summary=commit_obj.tree.context.summary,
        open_questions=commit_obj.tree.context.open_questions,
        topics=commit_obj.tree.context.topics,
    )


def read_commit(store: BaseStore, sha: str | None = None) -> Commit:
    return _resolve_commit(store, sha)


def log_commits(store: BaseStore, limit: int = 10) -> list[LogEntry]:
    if limit <= 0:
        return []
    sha = _head_sha(store)
    entries: list[LogEntry] = []
    while sha and len(entries) < limit:
        commit_obj = store.read_commit(sha)
        entries.append(
            LogEntry(
                sha=sha,
                message=commit_obj.message,
                author_model=commit_obj.author.model,
                timestamp=commit_obj.timestamp,
            )
        )
        sha = commit_obj.parent
    return entries


def stage(
    store: BaseStore,
    messages: list[dict],
    model: str,
    provider: str,
    decisions: list[dict] | None = None,
    open_questions: list[str] | None = None,
    artifacts: list[dict] | None = None,
    topics: list[str] | None = None,
) -> StageResult:
    session = _get_or_create_session(store)
    session.active_model = model
    session.active_provider = provider

    for raw in messages:
        msg = Message(**raw)
        if msg.role == "assistant":
            msg.model = model
            msg.provider = provider
        session.staged_messages.append(msg)

    if decisions:
        session.staged_decisions.extend(
            Decision(**d) if isinstance(d, dict) else d for d in decisions
        )
    if open_questions:
        session.staged_open_questions.extend(open_questions)
    if artifacts:
        session.staged_artifacts.extend(
            Artifact(**a) if isinstance(a, dict) else a for a in artifacts
        )
    if topics:
        session.staged_topics.extend(topics)

    store.write_session(session)
    return StageResult(
        staged_messages=len(session.staged_messages),
        session_id=session.session_id,
    )


def commit(
    store: BaseStore,
    message: str,
    summary: str,
    model: str,
    provider: str,
    topics: list[str] | None = None,
) -> CommitResult:
    session = _get_or_create_session(store)
    parent_sha = _head_sha(store)

    parent_ctx = Context()
    parent_messages: list[Message] = []
    if parent_sha:
        parent_commit = store.read_commit(parent_sha)
        parent_ctx = parent_commit.tree.context
        parent_messages = parent_commit.tree.messages

    merged_ctx = Context(
        summary=summary,
        decisions=parent_ctx.decisions + session.staged_decisions,
        open_questions=_dedupe_preserve_order(
            parent_ctx.open_questions + session.staged_open_questions
        ),
        artifacts=parent_ctx.artifacts + session.staged_artifacts,
        topics=_dedupe_preserve_order(
            parent_ctx.topics + session.staged_topics + (topics or [])
        ),
    )

    all_messages = parent_messages + session.staged_messages
    new_commit = Commit(
        sha="",
        parent=parent_sha,
        message=message,
        author=Author(model=model, provider=provider, session_id=session.session_id),
        tree=CommitTree(
            messages=all_messages,
            context=merged_ctx,
            metadata=CommitMetadata(
                token_count=sum(len(m.content) for m in session.staged_messages),
            ),
        ),
    )
    new_commit.sha = sha_for_commit(new_commit)

    store.write_commit(new_commit)
    branch = store.read_head()
    store.write_ref(branch, new_commit.sha)

    # Reset staging area, preserve session identity.
    session.staged_messages = []
    session.staged_decisions = []
    session.staged_open_questions = []
    session.staged_artifacts = []
    session.staged_topics = []
    session.head_commit = new_commit.sha
    store.write_session(session)

    log.info("Committed %s on branch '%s': %s", new_commit.sha, branch, message)
    return CommitResult(sha=new_commit.sha, branch=branch, message=message)


def create_tag(store: BaseStore, name: str, sha: str | None = None) -> TagResult:
    tag_name = _validate_tag_name(name)
    target_sha = resolve_ref(store, sha) if sha else _head_sha(store)
    if not target_sha:
        raise ValueError("No commits yet.")
    store.write_tag(tag_name, target_sha)
    return TagResult(name=tag_name, sha=target_sha)


def list_tags(store: BaseStore) -> TagListResult:
    tags: list[TagResult] = []
    for name in sorted(store.list_tags()):
        sha = store.read_tag(name)
        if sha:
            tags.append(TagResult(name=name, sha=sha))
    return TagListResult(tags=tags)


def read_window(
    store: BaseStore,
    sha: str | None = None,
    last_n: int = 20,
    before_index: int | None = None,
) -> ReadWindowResult:
    if last_n <= 0:
        raise ValueError("last_n must be > 0")

    commit_obj = _resolve_commit(store, sha)
    messages = commit_obj.tree.messages
    total = len(messages)

    end = total if before_index is None else max(0, min(before_index, total))
    start = max(0, end - last_n)
    window = messages[start:end]

    return ReadWindowResult(
        sha=commit_obj.sha,
        start_index=start,
        end_index=end,
        total_messages=total,
        messages=window,
    )


def read_for_budget(
    store: BaseStore,
    sha: str | None = None,
    max_chars: int = 12000,
) -> ReadBudgetResult:
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    commit_obj = _resolve_commit(store, sha)
    messages = commit_obj.tree.messages
    total = len(messages)

    selected_newest_first: list[Message] = []
    used_chars = 0
    for msg in reversed(messages):
        msg_size = len(msg.content)
        if used_chars + msg_size > max_chars:
            if not selected_newest_first:
                selected_newest_first.append(msg)
                used_chars += msg_size
            break
        selected_newest_first.append(msg)
        used_chars += msg_size

    selected = list(reversed(selected_newest_first))
    start_index = total - len(selected)

    return ReadBudgetResult(
        sha=commit_obj.sha,
        max_chars=max_chars,
        used_chars=used_chars,
        start_index=start_index,
        end_index=total,
        total_messages=total,
        messages=selected,
    )


def _score_text(text: str, terms: list[str]) -> int:
    normalized = text.lower()
    return sum(normalized.count(term) for term in terms)


def search(store: BaseStore, query: str, limit: int = 5) -> SearchResult:
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit <= 0:
        return SearchResult(query=query, hits=[])

    terms = _SEARCH_TERM_RE.findall(query.lower())
    if not terms:
        return SearchResult(query=query, hits=[])

    sha = _head_sha(store)
    depth = 0
    candidates: list[tuple[float, int, SearchHit]] = []

    while sha:
        commit_obj = store.read_commit(sha)
        ctx = commit_obj.tree.context

        score = 0.0
        score += _score_text(commit_obj.message, terms) * 6
        score += _score_text(ctx.summary, terms) * 5
        score += _score_text(" ".join(ctx.topics), terms) * 4
        score += _score_text(
            " ".join(f"{d.decision} {d.rationale}" for d in ctx.decisions), terms
        ) * 3
        score += _score_text(" ".join(m.content for m in commit_obj.tree.messages), terms) * 1

        if score > 0:
            candidates.append(
                (
                    score,
                    depth,
                    SearchHit(
                        sha=commit_obj.sha,
                        score=score,
                        message=commit_obj.message,
                        summary=ctx.summary,
                        topics=ctx.topics,
                    ),
                )
            )

        sha = commit_obj.parent
        depth += 1

    candidates.sort(key=lambda x: (-x[0], x[1]))
    return SearchResult(query=query, hits=[item[2] for item in candidates[:limit]])


async def _call_llm(prompt: str) -> str:
    provider = os.getenv("COMPACT_PROVIDER", "").lower()
    model = os.getenv("COMPACT_MODEL", "")
    api_key = (
        os.getenv("COMPACT_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY", "")
    )
    base_url = os.getenv("COMPACT_BASE_URL", "")

    if provider == "anthropic":
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model": model or "claude-haiku-4-5",
                    "max_tokens": 512,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]

    if provider in ("openai", "openai_compatible"):
        url = (
            base_url.rstrip("/") + "/chat/completions"
            if base_url
            else "https://api.openai.com/v1/chat/completions"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model or "gpt-4o-mini",
                    "max_tokens": 512,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    raise ValueError(
        f"Unknown COMPACT_PROVIDER='{provider}'. "
        "Set it to 'anthropic', 'openai', or 'openai_compatible'."
    )


async def _run_compaction(store: BaseStore, sha: str) -> None:
    try:
        commit_obj = store.read_commit(sha)
        messages = commit_obj.tree.messages
        to_summarize = messages[:-COMPACT_KEEP_LAST]
        keep = messages[-COMPACT_KEEP_LAST:]

        conversation_text = "\n".join(
            f"{m.role.upper()}: {m.content}" for m in to_summarize
        )
        prompt = (
            "You are summarizing an older portion of a conversation for long-term memory storage.\n"
            "Preserve: key decisions made, facts established, open questions, technical choices.\n"
            "Discard: pleasantries, repetition, exploratory tangents that went nowhere.\n"
            "Output a dense 3-5 sentence paragraph. No bullet points.\n\n"
            f"CONVERSATION TO SUMMARIZE:\n{conversation_text}"
        )

        summary_text = await _call_llm(prompt)
        compaction_marker = Message(
            role="system",
            content=f"[COMPACTED - {len(to_summarize)} messages summarized]\n\n{summary_text}",
            ts=_now_utc(),
        )

        # Deliberate exception: compaction mutates the existing commit object in place.
        commit_obj.tree.messages = [compaction_marker] + keep
        commit_obj.tree.metadata.compacted = True
        commit_obj.tree.metadata.compacted_up_to_index = len(to_summarize)
        store.write_commit(commit_obj)

        store.write_compaction_state(
            CompactionState(
                status="succeeded",
                sha=sha,
                started_at=store.read_compaction_state().started_at,
                completed_at=_now_utc(),
                error=None,
            )
        )
        log.info(
            "Compaction complete for %s: %d -> 1 + %d messages",
            sha,
            len(to_summarize),
            len(keep),
        )
    except Exception as exc:
        log.exception("Background compaction failed for sha=%s", sha)
        current = store.read_compaction_state()
        store.write_compaction_state(
            CompactionState(
                status="failed",
                sha=sha,
                started_at=current.started_at or _now_utc(),
                completed_at=_now_utc(),
                error=str(exc),
            )
        )


def compact(store: BaseStore) -> CompactResult:
    sha = _head_sha(store)
    if not sha:
        return CompactResult(status="no_commits", message="No commits yet.")

    state = store.read_compaction_state()
    if state.status == "running":
        return CompactResult(
            status="already_running",
            sha=state.sha,
            message="Compaction is already running.",
        )

    commit_obj = store.read_commit(sha)
    total = len(commit_obj.tree.messages)
    if total <= COMPACT_KEEP_LAST:
        return CompactResult(
            status="nothing_to_compact",
            sha=sha,
            remaining_message_count=total,
            message="Threshold not reached.",
        )

    store.write_compaction_state(
        CompactionState(
            status="running",
            sha=sha,
            started_at=_now_utc(),
            completed_at=None,
            error=None,
        )
    )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run_compaction(store, sha))
    except RuntimeError:
        threading.Thread(
            target=asyncio.run,
            args=(_run_compaction(store, sha),),
            daemon=True,
        ).start()

    return CompactResult(
        status="compaction_started",
        sha=sha,
        compacted_message_count=total - COMPACT_KEEP_LAST,
        remaining_message_count=COMPACT_KEEP_LAST,
        message="Compaction started.",
    )


def compaction_status(store: BaseStore) -> CompactionStatusResult:
    state = store.read_compaction_state()
    return CompactionStatusResult(
        status=state.status,
        sha=state.sha,
        started_at=state.started_at,
        completed_at=state.completed_at,
        error=state.error,
    )
