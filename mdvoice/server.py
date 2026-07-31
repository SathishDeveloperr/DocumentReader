"""FastAPI application: upload Markdown, pick a voice, get audio back."""

from __future__ import annotations

import io
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, library
from .engines import available_engines, get_engine

app = FastAPI(title="Markdown Voice Player", version="1.0.0")

PREVIEW_TEXT = (
    "Hello. This is how your document will sound with this voice. "
    "Adjust the speed and pitch until it feels right."
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class SynthesisRequest(BaseModel):
    voice: str
    engine: Optional[str] = None
    rate: int = Field(0, ge=-90, le=200)
    pitch: int = Field(0, ge=-100, le=100)
    volume: int = Field(0, ge=-90, le=100)


class PreviewRequest(BaseModel):
    voice: str
    engine: Optional[str] = None
    rate: int = Field(0, ge=-90, le=200)
    pitch: int = Field(0, ge=-100, le=100)
    text: Optional[str] = None


class PasteRequest(BaseModel):
    text: str
    name: str = "Pasted note.md"
    split_level: int = Field(2, ge=1, le=6)
    skip_code: bool = True
    skip_tables: bool = True
    announce_skips: bool = True


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
@app.get("/api/engines")
async def engines() -> dict:
    return {"engines": available_engines(), "active": config.ENGINE}


@app.get("/api/voices")
async def voices(engine: Optional[str] = None, refresh: bool = Query(False)) -> dict:
    name = engine or config.ENGINE
    try:
        backend = get_engine(name)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    catalogue = await backend.list_voices(refresh=refresh)
    return {
        "engine": name,
        "count": len(catalogue),
        "offline_fallback": bool(catalogue and catalogue[0].get("offline_fallback")),
        "voices": catalogue,
    }


@app.post("/api/preview")
async def preview(payload: PreviewRequest):
    backend = get_engine(payload.engine or config.ENGINE)
    text = (payload.text or PREVIEW_TEXT)[:400]
    try:
        audio, _ = await backend.synthesize(
            text, payload.voice, rate=payload.rate, pitch=payload.pitch
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Voice preview failed: {exc}") from exc
    return StreamingResponse(io.BytesIO(audio), media_type=backend.mime_type)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@app.get("/api/documents")
async def documents() -> dict:
    return {"documents": library.list_documents()}


@app.post("/api/documents")
async def upload(
    file: UploadFile = File(...),
    split_level: int = Form(2),
    skip_code: bool = Form(True),
    skip_tables: bool = Form(True),
    announce_skips: bool = Form(True),
) -> dict:
    raw = await file.read()
    if len(raw) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than the 4 MB limit.")
    if not raw.strip():
        raise HTTPException(400, "That file is empty.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    try:
        record = library.create_document(
            text,
            file.filename or "document.md",
            split_level=split_level,
            skip_code=skip_code,
            skip_tables=skip_tables,
            announce_skips=announce_skips,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return record


@app.post("/api/documents/paste")
async def paste(payload: PasteRequest) -> dict:
    if not payload.text.strip():
        raise HTTPException(400, "Nothing to read.")
    try:
        return library.create_document(
            payload.text,
            payload.name,
            split_level=payload.split_level,
            skip_code=payload.skip_code,
            skip_tables=payload.skip_tables,
            announce_skips=payload.announce_skips,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/documents/{doc_id}")
async def document(doc_id: str) -> dict:
    record = library.load_document(doc_id)
    if record is None:
        raise HTTPException(404, "Document not found.")
    return record


@app.delete("/api/documents/{doc_id}")
async def remove(doc_id: str) -> dict:
    if not library.delete_document(doc_id):
        raise HTTPException(404, "Document not found.")
    return {"deleted": doc_id}


@app.post("/api/documents/{doc_id}/synthesize")
async def synthesize(doc_id: str, payload: SynthesisRequest) -> dict:
    try:
        job = library.start_synthesis(
            doc_id,
            engine_name=payload.engine or config.ENGINE,
            voice=payload.voice,
            rate=payload.rate,
            pitch=payload.pitch,
            volume=payload.volume,
        )
    except KeyError as exc:
        raise HTTPException(404, "Document not found.") from exc
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    job = library.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str) -> dict:
    job = library.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.task and not job.task.done():
        job.task.cancel()
    job.status = "cancelled"
    return job.to_dict()


@app.get("/api/documents/{doc_id}/audio")
async def audio(doc_id: str, download: bool = False):
    path = library.audio_path(doc_id)
    if path is None or not path.exists():
        raise HTTPException(404, "No audio yet - run the conversion first.")
    record = library.load_document(doc_id) or {}
    media = "audio/wav" if path.suffix == ".wav" else "audio/mpeg"
    safe = "".join(ch for ch in record.get("title", "audiobook") if ch.isalnum() or ch in " -_")
    return FileResponse(
        path,
        media_type=media,
        filename=f"{safe.strip() or 'audiobook'}{path.suffix}" if download else None,
    )


@app.get("/api/documents/{doc_id}/transcript")
async def transcript(doc_id: str):
    path = library.transcript_path(doc_id)
    if not path.exists():
        raise HTTPException(404, "No transcript yet.")
    return FileResponse(path, media_type="application/json")


@app.get("/api/samples/starter")
async def starter_sample() -> dict:
    path = config.SAMPLES_DIR / "sample.md"
    if not path.exists():
        raise HTTPException(404, "Sample missing.")
    return {"name": "sample.md", "text": path.read_text("utf-8")}


# ---------------------------------------------------------------------------
# Front end
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    page = config.STATIC_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>static/index.html is missing</h1>", status_code=500)
    return HTMLResponse(page.read_text("utf-8"))


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "engine": config.ENGINE})


if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
