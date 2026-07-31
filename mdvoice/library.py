"""Document storage and the synthesis job queue."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .engines import audio_duration, concat_audio, get_engine
from .markdown_parser import parse_markdown


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def _doc_dir(doc_id: str) -> Path:
    return config.WORKSPACE / doc_id


def create_document(
    source: str,
    filename: str,
    *,
    split_level: int = 2,
    skip_code: bool = True,
    skip_tables: bool = True,
    announce_skips: bool = True,
) -> dict:
    doc_id = uuid.uuid4().hex[:12]
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip() or "Untitled"
    parsed = parse_markdown(
        source,
        doc_title=stem,
        split_level=split_level,
        skip_code=skip_code,
        skip_tables=skip_tables,
        announce_skips=announce_skips,
    )
    if not parsed.chapters:
        raise ValueError("No readable text found in that Markdown file.")

    directory = _doc_dir(doc_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source.md").write_text(source, "utf-8")

    record = {
        "id": doc_id,
        "filename": filename,
        "created": time.time(),
        "options": {
            "split_level": split_level,
            "skip_code": skip_code,
            "skip_tables": skip_tables,
            "announce_skips": announce_skips,
        },
        **parsed.to_dict(),
    }
    (directory / "doc.json").write_text(json.dumps(record), "utf-8")
    return record


def load_document(doc_id: str) -> Optional[dict]:
    path = _doc_dir(doc_id) / "doc.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def list_documents() -> List[dict]:
    out = []
    for path in config.WORKSPACE.glob("*/doc.json"):
        try:
            record = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        audio = next(path.parent.glob("audio.*"), None)
        out.append(
            {
                "id": record["id"],
                "title": record["title"],
                "filename": record.get("filename", ""),
                "created": record.get("created", 0),
                "chapters": len(record.get("chapters", [])),
                "total_chars": record.get("total_chars", 0),
                "has_audio": audio is not None,
            }
        )
    return sorted(out, key=lambda d: d["created"], reverse=True)


def delete_document(doc_id: str) -> bool:
    directory = _doc_dir(doc_id)
    if not directory.exists():
        return False
    for item in directory.iterdir():
        item.unlink(missing_ok=True)
    directory.rmdir()
    return True


def audio_path(doc_id: str) -> Optional[Path]:
    return next(_doc_dir(doc_id).glob("audio.*"), None)


def transcript_path(doc_id: str) -> Path:
    return _doc_dir(doc_id) / "transcript.json"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    doc_id: str
    total: int
    status: str = "queued"          # queued | running | done | error | cancelled
    done: int = 0
    message: str = "Queued"
    error: str = ""
    result: Optional[dict] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "progress": round(self.done / self.total, 3) if self.total else 0.0,
            "message": self.message,
            "error": self.error,
            "result": self.result,
        }


JOBS: Dict[str, Job] = {}


def get_job(job_id: str) -> Optional[Job]:
    return JOBS.get(job_id)


def start_synthesis(
    doc_id: str,
    *,
    engine_name: str,
    voice: str,
    rate: int = 0,
    pitch: int = 0,
    volume: int = 0,
) -> Job:
    record = load_document(doc_id)
    if record is None:
        raise KeyError(doc_id)

    for existing in JOBS.values():
        if existing.doc_id == doc_id and existing.status in ("queued", "running"):
            if existing.task:
                existing.task.cancel()
            existing.status = "cancelled"

    job = Job(id=uuid.uuid4().hex[:12], doc_id=doc_id, total=len(record["chapters"]))
    JOBS[job.id] = job
    job.task = asyncio.create_task(
        _run(job, record, engine_name=engine_name, voice=voice, rate=rate, pitch=pitch, volume=volume)
    )
    return job


async def _run(
    job: Job,
    record: dict,
    *,
    engine_name: str,
    voice: str,
    rate: int,
    pitch: int,
    volume: int,
) -> None:
    job.status = "running"
    job.message = "Connecting to the voice engine…"
    engine = get_engine(engine_name)
    chapters = record["chapters"]
    semaphore = asyncio.Semaphore(max(1, config.MAX_PARALLEL_CHAPTERS))
    results: List[Optional[tuple]] = [None] * len(chapters)

    async def one(index: int, chapter: dict) -> None:
        async with semaphore:
            audio, words = await engine.synthesize(
                chapter["text"], voice, rate=rate, pitch=pitch, volume=volume
            )
            results[index] = (audio, words)
            job.done += 1
            job.message = f"Narrated “{chapter['title']}”"

    try:
        await asyncio.gather(*(one(i, c) for i, c in enumerate(chapters)))

        job.message = "Stitching tracks together…"
        pieces: List[bytes] = []
        tracks: List[dict] = []
        words_out: List[dict] = []
        cursor = 0.0
        for index, chapter in enumerate(chapters):
            audio, words = results[index] or (b"", [])
            length = audio_duration(audio, engine.audio_format)
            if length <= 0 and words:
                length = max(w.end for w in words)
            pieces.append(audio)
            tracks.append(
                {
                    "index": index,
                    "title": chapter["title"],
                    "level": chapter["level"],
                    "start": round(cursor, 3),
                    "end": round(cursor + length, 3),
                    "duration": round(length, 3),
                }
            )
            for word in words:
                words_out.append(
                    {
                        "t": word.text,
                        "s": round(cursor + word.start, 3),
                        "e": round(cursor + word.end, 3),
                        "c": index,
                    }
                )
            cursor += length

        directory = _doc_dir(job.doc_id)
        for stale in directory.glob("audio.*"):
            stale.unlink(missing_ok=True)
        out_path = directory / f"audio.{engine.audio_format}"
        out_path.write_bytes(concat_audio(pieces, engine.audio_format))

        result = {
            "audio_url": f"/api/documents/{job.doc_id}/audio?v={int(time.time())}",
            "mime": engine.mime_type,
            "format": engine.audio_format,
            "duration": round(cursor, 3),
            "voice": voice,
            "engine": engine_name,
            "rate": rate,
            "pitch": pitch,
            "tracks": tracks,
        }
        transcript_path(job.doc_id).write_text(
            json.dumps({**result, "words": words_out}), "utf-8"
        )
        job.result = result
        job.status = "done"
        job.message = "Ready to play"
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.message = "Cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.message = "Synthesis failed"
