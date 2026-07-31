"""Offline placeholder engine.

Generates a soft synthetic hum with realistic word timings instead of real
speech. It exists so the interface can be developed, demoed and tested on a
machine with no internet access - it is NOT a usable narrator.

Enable with:  MDVOICE_ENGINE=demo python run.py
"""

from __future__ import annotations

import io
import math
import re
import struct
import wave
from typing import List, Tuple

from . import TTSEngine, Word, register

SAMPLE_RATE = 22050
WORDS_PER_MINUTE = 165

DEMO_VOICES = [
    ("demo-alto", "Alto (demo)", "Female", "en-US", 210.0),
    ("demo-tenor", "Tenor (demo)", "Male", "en-US", 130.0),
    ("demo-soprano", "Soprano (demo)", "Female", "en-GB", 260.0),
    ("demo-bass", "Bass (demo)", "Male", "en-IN", 98.0),
]


@register
class DemoEngine(TTSEngine):
    name = "demo"
    label = "Offline demo tones (no speech, testing only)"
    audio_format = "wav"
    mime_type = "audio/wav"
    supports_pitch = True
    needs_network = False

    async def list_voices(self, refresh: bool = False) -> List[dict]:
        return [
            {
                "id": vid,
                "name": label,
                "locale": locale,
                "gender": gender,
                "multilingual": False,
                "styles": ["placeholder"],
                "categories": ["Testing"],
            }
            for vid, label, gender, locale, _ in DEMO_VOICES
        ]

    async def synthesize(
        self,
        text: str,
        voice: str,
        *,
        rate: int = 0,
        pitch: int = 0,
        volume: int = 0,
    ) -> Tuple[bytes, List[Word]]:
        base = dict((v[0], v[4]) for v in DEMO_VOICES).get(voice, 180.0)
        base *= 2 ** (pitch / 120.0)
        speed = max(0.4, 1 + rate / 100.0)
        gain = max(0.05, min(1.0, 0.35 * (1 + volume / 100.0)))

        tokens = [t for t in re.findall(r"\S+", text) if t]
        per_word = 60.0 / (WORDS_PER_MINUTE * speed)

        words: List[Word] = []
        samples: List[int] = []
        cursor = 0.0
        for index, token in enumerate(tokens):
            length = per_word * min(2.2, 0.55 + len(token) / 7.0)
            words.append(Word(text=token, start=cursor, end=cursor + length * 0.85))
            freq = base * (1 + 0.06 * math.sin(index / 2.3))
            count = int(length * SAMPLE_RATE)
            for n in range(count):
                t = n / SAMPLE_RATE
                envelope = math.sin(math.pi * min(1.0, n / max(1, count)))
                value = (
                    math.sin(2 * math.pi * freq * t)
                    + 0.35 * math.sin(4 * math.pi * freq * t)
                    + 0.15 * math.sin(6 * math.pi * freq * t)
                )
                samples.append(int(max(-1.0, min(1.0, value / 1.5)) * envelope * gain * 32767))
            if token[-1] in ".!?":
                samples.extend([0] * int(0.28 * SAMPLE_RATE))
                cursor += 0.28
            cursor += length

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return buffer.getvalue(), words
