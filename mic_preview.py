"""Keep a short in-memory array buffer for a user-requested A/B listen."""
import base64
from collections import deque
from io import BytesIO
import wave

import numpy as np

from localize import RATE
from speech_events import speech_audio

PREVIEW_SAMPLES = 4 * RATE


class RecentAudio:
    def __init__(self):
        self.chunks = deque()
        self.end = 0

    def append(self, origin, audio):
        end = origin + len(audio)
        if end <= self.end:
            self.chunks.clear()
            self.end = 0
        start = max(self.end, origin)
        self.chunks.append((start, audio[start - origin:]))
        self.end = end
        while self.chunks[0][0] + len(self.chunks[0][1]) <= end - PREVIEW_SAMPLES:
            self.chunks.popleft()

    def snapshot(self):
        if not self.chunks:
            return None
        start = max(self.chunks[0][0], self.end - PREVIEW_SAMPLES)
        audio = np.zeros((self.end - start, 8), dtype=np.int32)
        filled = 0
        for origin, chunk in self.chunks:
            offset = max(0, start - origin)
            sample = origin + offset - start
            count = len(chunk) - offset
            audio[sample:sample + count] = chunk[offset:]
            filled += count
        return audio, len(audio) - filled


def encoded_wav(signal, gain):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        writer.writeframes((gain * signal).astype("<i2").tobytes())
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def preview_pair(audio, missing, positions, direction, noise):
    raw, cleaned = speech_audio(audio, positions, direction, noise)
    # Apply the same playback gain so background loudness can be compared fairly.
    peak = max(np.max(np.abs(raw)), np.max(np.abs(cleaned)), 1000.)
    gain = 12000. / peak
    return {"raw": encoded_wav(raw, gain), "cleaned": encoded_wav(cleaned, gain),
            "duration": len(audio) / RATE, "missing_ms": round(1000 * missing / RATE),
            "noise_ready": noise is not None}
