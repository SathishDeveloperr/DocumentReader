"""Microsoft Edge neural voices via the `edge-tts` package.

Free, no API key, ~320 voices across ~140 locales. Needs an internet
connection because synthesis happens on Microsoft's servers.
"""

from __future__ import annotations

import json
import re
import time
from typing import List, Tuple

from .. import config
from ..fallback_voices import FALLBACK_VOICES
from . import TTSEngine, Word, register

VOICE_CACHE = config.CACHE_DIR / "edge_voices.json"

# "Microsoft Aria Online (Natural) - English (United States)" -> "Aria"
FRIENDLY_RE = re.compile(r"Microsoft\s+(.+?)\s+Online", re.I)


def _display_name(short_name: str, friendly: str) -> str:
    match = FRIENDLY_RE.search(friendly or "")
    if match:
        return match.group(1).strip()
    stem = short_name.split("-")[-1]
    stem = re.sub(r"(Multilingual)?Neural.*$", "", stem)
    return re.sub(r"(?<!^)(?=[A-Z])", " ", stem).strip() or short_name


def _normalise(raw: dict) -> dict:
    short = raw.get("ShortName") or raw.get("Name", "")
    tag = raw.get("VoiceTag") or {}
    return {
        "id": short,
        "name": _display_name(short, raw.get("FriendlyName", "")),
        "locale": raw.get("Locale", ""),
        "gender": raw.get("Gender", ""),
        "multilingual": "Multilingual" in short,
        "styles": [s for s in (tag.get("VoicePersonalities") or []) if s][:4],
        "categories": [c for c in (tag.get("ContentCategories") or []) if c][:3],
    }


@register
class EdgeEngine(TTSEngine):
    name = "edge"
    label = "Microsoft Edge neural voices (free)"
    audio_format = "mp3"
    mime_type = "audio/mpeg"
    supports_pitch = True
    needs_network = True

    # -- voices -------------------------------------------------------------
    async def list_voices(self, refresh: bool = False) -> List[dict]:
        if not refresh and VOICE_CACHE.exists():
            age = time.time() - VOICE_CACHE.stat().st_mtime
            if age < config.VOICE_CACHE_TTL:
                try:
                    cached = json.loads(VOICE_CACHE.read_text("utf-8"))
                    if cached.get("voices"):
                        return cached["voices"]
                except Exception:
                    pass
        try:
            import edge_tts

            raw = await edge_tts.list_voices()
            voices = sorted(
                (_normalise(v) for v in raw),
                key=lambda v: (v["locale"], v["name"]),
            )
            if voices:
                VOICE_CACHE.write_text(
                    json.dumps({"fetched": time.time(), "voices": voices}), "utf-8"
                )
                return voices
        except Exception:
            pass

        if VOICE_CACHE.exists():  # stale cache beats no cache
            try:
                return json.loads(VOICE_CACHE.read_text("utf-8"))["voices"]
            except Exception:
                pass
        return [
            {
                "id": short,
                "name": label,
                "locale": locale,
                "gender": gender,
                "multilingual": "Multilingual" in short,
                "styles": [],
                "categories": [],
                "offline_fallback": True,
            }
            for short, gender, locale, label in FALLBACK_VOICES
        ]

    # -- synthesis ----------------------------------------------------------
    async def synthesize(
        self,
        text: str,
        voice: str,
        *,
        rate: int = 0,
        pitch: int = 0,
        volume: int = 0,
    ) -> Tuple[bytes, List[Word]]:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=f"{rate:+d}%",
            pitch=f"{pitch:+d}Hz",
            volume=f"{volume:+d}%",
            boundary="WordBoundary",
        )
        audio = bytearray()
        words: List[Word] = []
        async for chunk in communicate.stream():
            kind = chunk.get("type")
            if kind == "audio":
                audio.extend(chunk["data"])
            elif kind == "WordBoundary":
                start = chunk.get("offset", 0) / 1e7
                dur = chunk.get("duration", 0) / 1e7
                words.append(Word(text=chunk.get("text", ""), start=start, end=start + dur))
        return bytes(audio), words
