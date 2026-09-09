from __future__ import annotations

import json
from collections.abc import AsyncIterator

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.db import store
from app.agent.loop import _dbg, run_turn
from app.llm.client import check_ollama


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init_db()
    _dbg("BOOT", "main.py:lifespan", "instrumented uvicorn started", {"port": 8000})
    yield


app = FastAPI(title="Local Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.web_dir), name="static")


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.web_dir / "index.html")


@app.get("/api/health")
async def health() -> dict:
    status = await check_ollama()
    return status


@app.get("/api/sessions")
def sessions() -> list[dict]:
    return store.list_sessions()


@app.post("/api/sessions")
def create_session() -> dict:
    return store.create_session()


@app.delete("/api/sessions/{session_id}")
def remove_session(session_id: str) -> dict:
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
def messages(session_id: str) -> list[dict]:
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return store.list_messages(session_id)


@app.post("/api/sessions/{session_id}/messages")
async def send_message(session_id: str, body: ChatIn) -> StreamingResponse:
    if store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")

    async def events() -> AsyncIterator[bytes]:
        async for event in run_turn(session_id, body.content.strip()):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
