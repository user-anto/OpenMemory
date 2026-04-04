from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from gui.service import MemoryGuiService

load_dotenv()

app = FastAPI(title="llm-memory-gui", version="0.1.0")
service = MemoryGuiService()

_STATIC_DIR = Path(__file__).parent / "static"
_ROOT_LOGO = Path.cwd() / "OpenMem logo.png"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


class SettingsUpdateRequest(BaseModel):
    memory_store_path: str


class StartSessionRequest(BaseModel):
    name: str


class UseSessionRequest(BaseModel):
    query: str


class LinkModelRequest(BaseModel):
    session_slug: str
    model_id: str


class UnlinkModelRequest(BaseModel):
    session_slug: str
    action: str | None = None
    commit_message: str | None = None
    summary: str | None = None


class ChatRequest(BaseModel):
    user_message: str
    session_slug: str | None = None


class CommitRequest(BaseModel):
    message: str | None = None
    summary: str | None = None
    session_slug: str | None = None
    topics: list[str] | None = None


class DiscardRequest(BaseModel):
    session_slug: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/assets/openmem-logo")
def openmem_logo() -> FileResponse:
    if not _ROOT_LOGO.exists():
        raise HTTPException(status_code=404, detail="OpenMem logo not found")
    return FileResponse(_ROOT_LOGO)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/settings")
def get_settings() -> dict:
    return service.settings()


@app.post("/api/settings")
def update_settings(req: SettingsUpdateRequest) -> dict:
    try:
        return service.update_memory_store_path(req.memory_store_path)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/settings/browse")
def browse_settings_path() -> dict:
    try:
        return service.browse_memory_store_path()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sessions")
def list_sessions() -> dict:
    try:
        return service.list_sessions()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sessions/current")
def current_session() -> dict:
    try:
        return service.current_session()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/start")
def start_session(req: StartSessionRequest) -> dict:
    try:
        return service.start_session(req.name)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/use")
def use_session(req: UseSessionRequest) -> dict:
    try:
        return service.use_session(req.query)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/link")
def link_session(req: LinkModelRequest) -> dict:
    try:
        return service.link_model(req.session_slug, req.model_id)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/unlink")
def unlink_session(req: UnlinkModelRequest) -> dict:
    try:
        return service.unlink_model(
            session_slug=req.session_slug,
            action=req.action,
            commit_message=req.commit_message,
            summary=req.summary,
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status")
def get_status(session_slug: str | None = None) -> dict:
    try:
        return service.status(session_slug=session_slug)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/commits")
def list_commits(limit: int = 50, session_slug: str | None = None) -> list[dict]:
    try:
        return service.list_commits(limit=limit, session_slug=session_slug)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/commits/{sha}")
def get_commit(sha: str, session_slug: str | None = None) -> dict:
    try:
        return service.get_commit(sha=sha, session_slug=session_slug)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/chat/send")
def send_chat(req: ChatRequest) -> dict:
    try:
        return service.send_chat(
            user_message=req.user_message,
            session_slug=req.session_slug,
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/chat/commit")
def commit_chat(req: CommitRequest) -> dict:
    try:
        return service.commit_staged(
            message=req.message,
            summary=req.summary,
            session_slug=req.session_slug,
            topics=req.topics,
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/chat/discard")
def discard_chat(req: DiscardRequest) -> dict:
    try:
        return service.discard_staged(session_slug=req.session_slug)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def main() -> None:
    import uvicorn

    host = os.getenv("MEMORY_GUI_HOST", "127.0.0.1")
    port = int(os.getenv("MEMORY_GUI_PORT", "8765"))
    uvicorn.run("gui.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
