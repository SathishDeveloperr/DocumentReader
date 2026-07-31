"""Runtime configuration. Everything is overridable with environment vars."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("MDVOICE_WORKSPACE", BASE_DIR / "workspace")).resolve()
CACHE_DIR = WORKSPACE / ".cache"
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = BASE_DIR / "samples"

# "edge" = Microsoft Edge neural voices (free, needs internet).
# "demo" = offline tone generator, only for developing the UI without network.
ENGINE = os.environ.get("MDVOICE_ENGINE", "edge").strip().lower()

HOST = os.environ.get("MDVOICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MDVOICE_PORT", "8000"))

# Guard rails
MAX_UPLOAD_BYTES = int(os.environ.get("MDVOICE_MAX_UPLOAD", str(4 * 1024 * 1024)))
MAX_PARALLEL_CHAPTERS = int(os.environ.get("MDVOICE_CONCURRENCY", "3"))
VOICE_CACHE_TTL = int(os.environ.get("MDVOICE_VOICE_CACHE_TTL", str(60 * 60 * 24 * 7)))

WORKSPACE.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
