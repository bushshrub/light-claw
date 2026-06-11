"""FastAPI web server for light-claw."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lightclaw.agent import AgentLoop
from lightclaw.config import Config, TUI_LOCK_FILE, get_config
from lightclaw.memory import Workspace
from lightclaw.mcp import MCPManager
from lightclaw.tools.registry import get_default_registry

WEBUI_DIR = Path(__file__).parent.parent.parent / "lightclaw-webui" / "build"


class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"
    attachments: list[dict[str, Any]] = []


class MemorySetRequest(BaseModel):
    key: str
    value: str


class WebSession:
    def __init__(
        self,
        config: Config,
        workspace: Workspace | None = None,
        registry=None,
    ) -> None:
        self.config = config
        self._owns_workspace = workspace is None
        self.workspace = workspace if workspace is not None else Workspace(config)
        self.registry = registry if registry is not None else get_default_registry()
        self.mcp = MCPManager()
        self.agent = AgentLoop(config, self.registry, self.workspace)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._owns_workspace:
            await self.workspace.open()
        await self.mcp.start(self.registry)

    async def stop(self) -> None:
        await self.mcp.stop()
        if self._owns_workspace:
            await self.workspace.close()


_session: WebSession | None = None


def _get() -> WebSession:
    if _session is None:
        raise RuntimeError("session not initialized")
    return _session


def _tui_lock_for_thread(thread_id: str) -> str | None:
    """Return the TUI lock owner PID if the given thread is locked by the TUI, else None."""
    try:
        with open(TUI_LOCK_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    locked_thread = data.get("thread")
    if locked_thread and locked_thread == thread_id:
        return str(data.get("pid", "?"))
    return None


def create_app(
    config: Config | None = None,
    workspace: Workspace | None = None,
    registry=None,
) -> FastAPI:
    global _session
    cfg = config or get_config()
    _session = WebSession(cfg, workspace=workspace, registry=registry)

    app = FastAPI(title="light-claw")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        await _get().start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await _get().stop()

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> StreamingResponse:
        session = _get()

        lock_pid = _tui_lock_for_thread(req.thread_id)
        if lock_pid is not None:
            async def _locked():
                yield f"data: {json.dumps({'error': f'Thread {req.thread_id!r} is locked by TUI (pid {lock_pid}). Open the TUI to release it or use a different thread.'})}\n\n"
            return StreamingResponse(
                _locked(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                status_code=423,
            )

        attachments: list[dict[str, Any]] = []
        for att in req.attachments:
            if isinstance(att.get("data"), str):
                att = dict(att)
                try:
                    att["data"] = base64.b64decode(att["data"])
                except Exception:
                    att.pop("data", None)
            attachments.append(att)

        async def generate():
            async with session._lock:
                try:
                    async for chunk in session.agent.stream(
                        req.message,
                        thread_id=req.thread_id,
                        attachments=attachments or None,
                    ):
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                    stats = session.agent.token_stats
                    ctx = session.agent.context_length
                    yield f"data: {json.dumps({'done': True, 'tokens': stats, 'context_length': ctx})}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/history/{thread_id:path}")
    async def get_history(thread_id: str):
        messages = await _get().workspace.get_history(thread_id)
        return {"messages": messages}

    @app.delete("/api/history/{thread_id:path}")
    async def clear_history(thread_id: str):
        lock_pid = _tui_lock_for_thread(thread_id)
        if lock_pid is not None:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": f"Thread {thread_id!r} is locked by TUI (pid {lock_pid})."},
                status_code=423,
            )
        await _get().workspace.clear_history(thread_id)
        return {"ok": True}

    @app.get("/api/tools")
    def get_tools():
        session = _get()
        return {
            "tools": [
                {
                    "name": s["function"]["name"],
                    "description": s["function"].get("description", ""),
                }
                for s in session.registry.schemas()
            ]
        }

    @app.get("/api/model")
    async def get_model():
        session = _get()
        ctx = session.agent.context_length
        if ctx is None:
            try:
                ctx = await session.agent._llm.fetch_context_length()
            except Exception:
                pass
        return {"model": cfg.model, "base_url": cfg.base_url, "context_length": ctx}

    @app.get("/api/tokens")
    def get_tokens():
        session = _get()
        return {
            "tokens": session.agent.token_stats,
            "context_length": session.agent.context_length,
        }

    @app.get("/api/memory")
    async def list_memory():
        notes = await _get().workspace.list_notes()
        return {"notes": notes}

    @app.post("/api/memory")
    async def set_memory(req: MemorySetRequest):
        await _get().workspace.remember(req.key, req.value)
        return {"ok": True}

    @app.delete("/api/memory/{key}")
    async def delete_memory(key: str):
        ok = await _get().workspace.forget(key)
        return {"ok": ok}

    @app.get("/api/readonly")
    async def get_readonly():
        try:
            with open(TUI_LOCK_FILE) as f:
                data = json.load(f)
            return {"readonly": True, "pid": data.get("pid"), "thread": data.get("thread")}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"readonly": False, "pid": None, "thread": None}

    @app.post("/api/upload")
    async def upload_file(file: UploadFile = File(...)):
        data = await file.read()
        mime = file.content_type or "application/octet-stream"
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "filename": file.filename,
            "mime_type": mime,
            "data": b64,
            "size": len(data),
        }

    @app.post("/api/v1/audio/upload")
    async def upload_audio(file: UploadFile = File(...)):
        data = await file.read()
        mime = file.content_type or "application/octet-stream"
        
        # Validate audio file types
        audio_mimes = {"audio/wav", "audio/webm", "audio/mp3", "audio/mpeg", "audio/x-m4a", "audio/flac"}
        if mime not in audio_mimes:
            raise HTTPException(status_code=400, detail=f"Unsupported audio format: {mime}")
        
        # Generate unique filename
        file_ext = mime.split("/")[-1]
        if file_ext == "mpeg":
            file_ext = "mp3"
        unique_id = str(uuid.uuid4())
        filename = f"{unique_id}.{file_ext}"
        
        # Save to temporary directory
        temp_dir = Path("/tmp/lightclaw_audio")
        temp_dir.mkdir(parents=True, exist_ok=True)
        file_path = temp_dir / filename
        
        with open(file_path, "wb") as f:
            f.write(data)
        
        return {
            "filename": filename,
            "original_filename": file.filename,
            "mime_type": mime,
            "size": len(data),
            "file_path": str(file_path),
            "id": unique_id,
        }

    # Frontend static files — must be registered last
    if WEBUI_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEBUI_DIR), html=True), name="static")

    return app
