"""
CLI for llm-memory. Usage: memory <command>
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import typer
from rich.console import Console
from rich.table import Table

from memory import engine
from memory.store import get_store

app = typer.Typer(help="llm-memory - inspect and manage your LLM conversation memory.")
session_app = typer.Typer(help="Manage named conversation sessions.")
app.add_typer(session_app, name="session")
console = Console()
store = get_store()


@app.command()
def status():
    """Show current branch, HEAD commit, and context summary."""
    s = engine.status(store)
    if s.session_name and s.session_slug:
        console.print(f"\n[bold]Session:[/bold] {s.session_name} ({s.session_slug})")
    console.print(f"\n[bold]Branch:[/bold] {s.branch}")
    console.print(f"[bold]HEAD:[/bold]   {s.head or 'none'}")
    if s.message:
        console.print(f"[bold]Commit:[/bold] {s.message}")
    if s.author_model:
        console.print(f"[bold]Author:[/bold] {s.author_model}")
    console.print(f"\n[bold]Summary:[/bold]\n{s.summary}")
    if s.open_questions:
        console.print("\n[bold]Open questions:[/bold]")
        for q in s.open_questions:
            console.print(f"  - {q}")
    if s.topics:
        console.print(f"\n[bold]Topics:[/bold] {', '.join(s.topics)}")
    console.print()


@app.command("log")
def log_cmd(limit: int = typer.Option(10, "--limit", "-n", help="Max commits to show.")):
    """Show recent commit history."""
    entries = engine.log_commits(store, limit)
    if not entries:
        console.print("[yellow]No commits yet.[/yellow]")
        raise typer.Exit()

    table = Table(show_header=True, header_style="bold")
    table.add_column("SHA", style="cyan", width=14)
    table.add_column("Model", width=22)
    table.add_column("Date", width=20)
    table.add_column("Message")
    for e in entries:
        table.add_row(
            e.sha,
            e.author_model,
            e.timestamp.strftime("%Y-%m-%d %H:%M"),
            e.message,
        )
    console.print(table)


@app.command()
def show(ref: str | None = typer.Argument(None, help="Commit SHA or tag. Defaults to HEAD.")):
    """Show the full content of a commit."""
    try:
        commit_obj = engine.read_commit(store, ref)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]commit {commit_obj.sha}[/bold cyan]")
    console.print(
        f"[bold]author:[/bold]  {commit_obj.author.model} ({commit_obj.author.provider})"
    )
    console.print(
        f"[bold]date:[/bold]    {commit_obj.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    console.print(f"[bold]parent:[/bold]  {commit_obj.parent or 'none'}")
    console.print(f"\n    {commit_obj.message}\n")
    console.print("[bold]Context summary:[/bold]")
    console.print(commit_obj.tree.context.summary or "(empty)")

    if commit_obj.tree.context.decisions:
        console.print("\n[bold]Decisions:[/bold]")
        for d in commit_obj.tree.context.decisions:
            console.print(f"  [{d.id}] {d.decision}")
            console.print(f"      {d.rationale}")

    if commit_obj.tree.context.open_questions:
        console.print("\n[bold]Open questions:[/bold]")
        for q in commit_obj.tree.context.open_questions:
            console.print(f"  - {q}")

    console.print(f"\n[bold]Messages:[/bold] {len(commit_obj.tree.messages)} total")
    console.print()


@app.command()
def branch(name: str = typer.Argument(..., help="New branch name.")):
    """Create a new branch from HEAD."""
    current = store.read_head()
    sha = store.read_ref(current)
    if not sha:
        console.print("[red]No commits yet. Make a commit before branching.[/red]")
        raise typer.Exit(1)
    store.write_ref(name, sha)
    console.print(f"[green]Branch '{name}' created at {sha}[/green]")


@app.command()
def branches():
    """List all branches."""
    current = store.read_head()
    all_branches = store.list_branches()
    if not all_branches:
        console.print("[yellow]No branches yet.[/yellow]")
        return
    for b in sorted(all_branches):
        marker = "* " if b == current else "  "
        sha = store.read_ref(b) or "-"
        console.print(f"{marker}[bold]{b}[/bold]  {sha}")


@app.command()
def compact():
    """Trigger background compaction of old messages in HEAD commit."""
    result = engine.compact(store)
    if result.status == "no_commits":
        console.print("[yellow]No commits yet.[/yellow]")
    elif result.status == "nothing_to_compact":
        console.print(
            f"[yellow]Nothing to compact ({result.remaining_message_count} messages, threshold not reached).[/yellow]"
        )
    elif result.status == "already_running":
        console.print(f"[yellow]{result.message}[/yellow]")
    else:
        console.print(
            f"[green]Compaction started for {result.sha}.[/green] "
            f"{result.compacted_message_count} messages will be summarized in the background. "
            f"{result.remaining_message_count} messages kept verbatim."
        )


@app.command("compact-status")
def compact_status():
    """Show compaction lifecycle state."""
    state = engine.compaction_status(store)
    console.print(f"[bold]Status:[/bold] {state.status}")
    if state.sha:
        console.print(f"[bold]SHA:[/bold] {state.sha}")
    if state.started_at:
        console.print(
            f"[bold]Started:[/bold] {state.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    if state.completed_at:
        console.print(
            f"[bold]Completed:[/bold] {state.completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    if state.error:
        console.print(f"[red]Error:[/red] {state.error}")


@app.command()
def tag(
    name: str = typer.Argument(..., help="Tag name."),
    sha: str | None = typer.Argument(None, help="Commit SHA or tag. Defaults to HEAD."),
):
    """Create or update a tag."""
    try:
        result = engine.create_tag(store, name=name, sha=sha)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Tag '{result.name}' -> {result.sha}[/green]")


@app.command()
def tags():
    """List tags."""
    result = engine.list_tags(store)
    if not result.tags:
        console.print("[yellow]No tags yet.[/yellow]")
        return
    for t in result.tags:
        console.print(f"[bold]{t.name}[/bold]  {t.sha}")


@session_app.command("new")
def session_new(name: str = typer.Argument(..., help="Human-friendly unique session name.")):
    """Create and switch to a new named session."""
    try:
        result = engine.start_session(store, name=name)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(
        f"[green]Using session:[/green] {result.session.name} ({result.session.slug})"
    )


@session_app.command("use")
def session_use(
    name_or_slug: str = typer.Argument(..., help="Existing session name or slug (fuzzy match)."),
):
    """Switch to an existing named session."""
    try:
        result = engine.use_session(store, name_or_slug=name_or_slug)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    suffix = " [fuzzy match]" if result.fuzzy_match else ""
    console.print(
        f"[green]Using session:[/green] {result.session.name} ({result.session.slug}){suffix}"
    )


@session_app.command("current")
def session_current():
    """Show the currently selected session."""
    result = engine.current_session(store)
    console.print(f"[bold]{result.session.name}[/bold]  {result.session.slug}")


@session_app.command("list")
def session_list():
    """List all sessions and the current one."""
    result = engine.list_sessions(store)
    for s in result.sessions:
        marker = "* " if s.slug == result.current_slug else "  "
        console.print(f"{marker}[bold]{s.name}[/bold]  {s.slug}")


@app.command()
def window(
    sha: str | None = typer.Option(None, "--sha", help="Commit SHA or tag. Defaults to HEAD."),
    last_n: int = typer.Option(20, "--last-n", help="Number of messages to return."),
    before_index: int | None = typer.Option(
        None,
        "--before-index",
        help="Exclusive message index upper bound.",
    ),
):
    """Show a fixed message window."""
    try:
        result = engine.read_window(
            store,
            sha=sha,
            last_n=last_n,
            before_index=before_index,
        )
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(
        f"[bold]Commit:[/bold] {result.sha}  "
        f"[bold]Range:[/bold] [{result.start_index}, {result.end_index})  "
        f"[bold]Total:[/bold] {result.total_messages}"
    )
    for i, msg in enumerate(result.messages, start=result.start_index):
        console.print(f"{i:>5}  {msg.role:>9}: {msg.content}")


@app.command()
def budget(
    sha: str | None = typer.Option(None, "--sha", help="Commit SHA or tag. Defaults to HEAD."),
    max_chars: int = typer.Option(
        12000,
        "--max-chars",
        help="Character budget for newest-first retrieval.",
    ),
):
    """Show newest messages that fit in a character budget."""
    try:
        result = engine.read_for_budget(store, sha=sha, max_chars=max_chars)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(
        f"[bold]Commit:[/bold] {result.sha}  "
        f"[bold]Used:[/bold] {result.used_chars}/{result.max_chars} chars  "
        f"[bold]Range:[/bold] [{result.start_index}, {result.end_index})"
    )
    for i, msg in enumerate(result.messages, start=result.start_index):
        console.print(f"{i:>5}  {msg.role:>9}: {msg.content}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(5, "--limit", "-n", help="Maximum number of hits."),
):
    """Search commit history on the current branch."""
    try:
        result = engine.search(store, query=query, limit=limit)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if not result.hits:
        console.print("[yellow]No matches.[/yellow]")
        raise typer.Exit()

    table = Table(show_header=True, header_style="bold")
    table.add_column("SHA", style="cyan", width=14)
    table.add_column("Score", width=8)
    table.add_column("Message", width=32)
    table.add_column("Topics")
    for hit in result.hits:
        table.add_row(
            hit.sha,
            f"{hit.score:.1f}",
            hit.message,
            ", ".join(hit.topics),
        )
    console.print(table)


if __name__ == "__main__":
    app()
