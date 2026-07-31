"""Pluggable text-to-speech engines.

Adding another engine (Piper, ElevenLabs, OpenAI, Coqui...) means writing one
class with `list_voices` and `synthesize`, then registering it below. Nothing
else in the app needs to change.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple, Type


@dataclass
class Word:
    """One spoken word and where it lands in the audio (seconds)."""

    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return {"t": self.text, "s": round(self.start, 3), "e": round(self.end, 3)}


class TTSEngine:
    """Base class for a text-to-speech backend."""

    name: str = "base"
    label: str = "Base engine"
    audio_format: str = "mp3"
    mime_type: str = "audio/mpeg"
    supports_pitch: bool = True
    needs_network: bool = True

    async def list_voices(self, refresh: bool = False) -> List[dict]:
        raise NotImplementedError

    async def synthesize(
        self,
        text: str,
        voice: str,
        *,
        rate: int = 0,
        pitch: int = 0,
        volume: int = 0,
    ) -> Tuple[bytes, List[Word]]:
        """Return (audio bytes, word timings) for one chunk of text."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Audio helpers shared by engines
# ---------------------------------------------------------------------------
def concat_audio(chunks: Sequence[bytes], fmt: str) -> bytes:
    """Join same-format audio chunks into one file."""
    chunks = [c for c in chunks if c]
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    if fmt == "wav":
        out = io.BytesIO()
        with wave.open(io.BytesIO(chunks[0]), "rb") as first:
            params = first.getparams()
        with wave.open(out, "wb") as writer:
            writer.setparams(params)
            for chunk in chunks:
                with wave.open(io.BytesIO(chunk), "rb") as reader:
                    writer.writeframes(reader.readframes(reader.getnframes()))
        return out.getvalue()
    # MP3 frames concatenate cleanly for constant-format streams like edge-tts.
    return b"".join(chunks)


def audio_duration(data: bytes, fmt: str) -> float:
    """Best-effort duration in seconds for an in-memory audio file."""
    if not data:
        return 0.0
    if fmt == "wav":
        try:
            with wave.open(io.BytesIO(data), "rb") as reader:
                return reader.getnframes() / float(reader.getframerate() or 1)
        except Exception:
            return 0.0
    try:
        from mutagen.mp3 import MP3

        return float(MP3(io.BytesIO(data)).info.length)
    except Exception:
        # Fall back to a rough estimate assuming a 24 kbps mono stream.
        return len(data) / 3000.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, Type[TTSEngine]] = {}
_INSTANCES: Dict[str, TTSEngine] = {}


def register(cls: Type[TTSEngine]) -> Type[TTSEngine]:
    _REGISTRY[cls.name] = cls
    return cls


def get_engine(name: str) -> TTSEngine:
    from . import demo, edge  # noqa: F401  (import registers the engines)

    key = (name or "edge").lower()
    if key not in _REGISTRY:
        raise KeyError(f"Unknown engine {name!r}. Available: {sorted(_REGISTRY)}")
    if key not in _INSTANCES:
        _INSTANCES[key] = _REGISTRY[key]()
    return _INSTANCES[key]


def available_engines() -> List[dict]:
    from . import demo, edge  # noqa: F401

    return [
        {
            "name": cls.name,
            "label": cls.label,
            "needs_network": cls.needs_network,
            "supports_pitch": cls.supports_pitch,
        }
        for cls in _REGISTRY.values()
    ]
