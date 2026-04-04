"""
MCP server entry point.

Each tool is a thin wrapper:  validate input → call engine → return result.
No business logic lives here.
"""
from __future__ import annotations

import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

from fastmcp import FastMCP
from memory import engine
from memory.store import get_store

mcp = FastMCP(
    name="llm-memory",
    instructions=(
        "Persistent, model-agnostic memory. "
        "Call memory_status at session start. "
        "Call memory_stage after each exchange. "
        "Call memory_commit before ending the session."
    ),
)

store = get_store()


@mcp.tool()
def memory_status() -> dict:
    """
    Call at the START of every session.
    Returns current branch, HEAD commit summary, open questions, and topics.
    """
    return engine.status(store).model_dump()


@mcp.tool()
def memory_start_session(name: str) -> dict:
    """
    Create and switch to a new named session namespace.
    Session names are globally unique.
    """
    return engine.start_session(store, name=name).model_dump()


@mcp.tool()
def memory_use_session(name_or_slug: str) -> dict:
    """
    Switch to an existing named session namespace.
    Supports fuzzy matching (e.g. \"Pizza\" -> \"Pizza Party\").
    """
    return engine.use_session(store, name_or_slug=name_or_slug).model_dump()


@mcp.tool()
def memory_current_session() -> dict:
    """
    Return the currently selected session namespace.
    """
    return engine.current_session(store).model_dump()


@mcp.tool()
def memory_list_sessions() -> dict:
    """
    List all named session namespaces and current selection.
    """
    return engine.list_sessions(store).model_dump()


@mcp.tool()
def memory_read(sha: str | None = None) -> dict:
    """
    Read the full commit tree (all messages + context).
    Defaults to HEAD. Pass a sha or tag to read a specific commit.
    """
    return engine.read_commit(store, sha).model_dump()


@mcp.tool()
def memory_log(limit: int = 10) -> list[dict]:
    """
    Return recent commit history for the current branch.
    """
    return [e.model_dump() for e in engine.log_commits(store, limit)]


@mcp.tool()
def memory_stage(
    messages: list[dict],
    model: str,
    provider: str,
    decisions: list[dict] | None = None,
    open_questions: list[str] | None = None,
    artifacts: list[dict] | None = None,
    topics: list[str] | None = None,
) -> dict:
    """
    Add messages and context updates to the staging area.
    Call after each meaningful exchange. Does NOT commit.

    messages: list of {role, content} dicts from this turn.
    model: your model name (self-reported, e.g. 'claude-sonnet-4-6').
    provider: your provider name (e.g. 'anthropic').
    decisions: list of {id, decision, rationale} dicts.
    open_questions: list of strings.
    artifacts: list of {type, path, description} dicts.
    topics: list of topic strings.
    """
    return engine.stage(
        store,
        messages=messages,
        model=model,
        provider=provider,
        decisions=decisions,
        open_questions=open_questions,
        artifacts=artifacts,
        topics=topics,
    ).model_dump()


@mcp.tool()
def memory_commit(
    message: str,
    summary: str,
    model: str,
    provider: str,
    topics: list[str] | None = None,
) -> dict:
    """
    Flush the staging area into an immutable commit.
    Call before ending the session or switching models.

    message: short description of what was accomplished (like a git commit message).
    summary: full updated context paragraph — not a diff, the whole picture.
    model: your model name.
    provider: your provider name.
    topics: optional list of topic strings to tag this commit with.
    """
    return engine.commit(
        store,
        message=message,
        summary=summary,
        model=model,
        provider=provider,
        topics=topics,
    ).model_dump()


@mcp.tool()
def memory_compact() -> dict:
    """
    Trigger background summarization of old messages to free context space.
    Returns immediately — compaction runs asynchronously.
    Call when the conversation is getting long.
    """
    return engine.compact(store).model_dump()


@mcp.tool()
def memory_compaction_status() -> dict:
    """
    Get current compaction state: idle/running/succeeded/failed.
    """
    return engine.compaction_status(store).model_dump()


@mcp.tool()
def memory_tag(name: str, sha: str | None = None) -> dict:
    """
    Create or update a lightweight tag that points to a commit.
    Defaults to tagging HEAD when sha is omitted.
    """
    return engine.create_tag(store, name=name, sha=sha).model_dump()


@mcp.tool()
def memory_tags() -> dict:
    """
    List all tags and the commit sha each tag points to.
    """
    return engine.list_tags(store).model_dump()


@mcp.tool()
def memory_read_window(
    sha: str | None = None,
    last_n: int = 20,
    before_index: int | None = None,
) -> dict:
    """
    Read a fixed-size message window from a commit (sha or tag, default HEAD).
    """
    return engine.read_window(
        store,
        sha=sha,
        last_n=last_n,
        before_index=before_index,
    ).model_dump()


@mcp.tool()
def memory_read_for_budget(sha: str | None = None, max_chars: int = 12000) -> dict:
    """
    Read the newest messages that fit within a character budget.
    """
    return engine.read_for_budget(
        store,
        sha=sha,
        max_chars=max_chars,
    ).model_dump()


@mcp.tool()
def memory_search(query: str, limit: int = 5) -> dict:
    """
    Search memory on the current branch using lexical scoring.
    """
    return engine.search(store, query=query, limit=limit).model_dump()


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
