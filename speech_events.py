"""Recognize a local voice trigger and locate the stable speech direction."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path

import numpy as np

from localize import HOP, RATE
from tracking import unit_vector

MODEL_NAME = "vosk-model-small-en-us-0.15"
KEYWORD_MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
COMMANDS = {"snake", "hey snake"}
CONFUSERS = ("snack", "sneak", "stake", "steak", "shake", "lake")
GRAMMAR = ["snake", "hey snake", *CONFUSERS, "[unk]"]
FRAME = 512
TAPER = np.hanning(FRAME)


class SnakeRecognizer:
    def __init__(self, model_path, keyword_model=None):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        try:
            self.model = Model(str(model_path))
        except Exception as exc:
            raise RuntimeError(f"Could not load the Vosk model at {model_path}") from exc
        self.recognizer = KaldiRecognizer
        self.spotter = None
        if keyword_model is not None:
            from sherpa_onnx import KeywordSpotter

            suffix = "-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
            paths = {part: str(keyword_model / (part + suffix)) for part in ("encoder", "decoder", "joiner")}
            if not all(Path(path).is_file() for path in [*paths.values(), keyword_model / "tokens.txt"]):
                raise ValueError(f"Incomplete keyword model at {keyword_model}")
            self.spotter = KeywordSpotter(**paths, tokens=str(keyword_model / "tokens.txt"), num_threads=1,
                                          keywords_file=str(Path(__file__).with_name("snake_keywords.txt")))

    def matches(self, signal):
        peak = np.max(np.abs(signal))
        if peak <= 0.:
            return False
        pcm = (signal * (30000. / peak)).astype("<i2")
        recognizer = self.recognizer(self.model, RATE, json.dumps(GRAMMAR))
        recognizer.SetWords(True)
        complete = recognizer.AcceptWaveform(pcm.tobytes())
        result = json.loads(recognizer.Result() if complete else recognizer.FinalResult())
        words = result.get("result", [])
        if result.get("text") in COMMANDS and any(
            word["word"] == "snake" and word["conf"] >= .65 and word["end"] - word["start"] >= .15
            for word in words
        ):
            return True
        if not self.spotter:
            return False
        if any(word["word"] in CONFUSERS and word["conf"] >= .8 for word in words):
            return False
        stream = self.spotter.create_stream()
        samples = (signal / peak).astype(np.float32)
        stream.accept_waveform(RATE, np.pad(samples, (3000, 12000)))
        stream.input_finished()
        while self.spotter.is_ready(stream):
            self.spotter.decode_stream(stream)
            if self.spotter.get_result(stream) == "SNAKE":
                return True
        return False


def stable_direction(estimates):
    if len(estimates) < 2:
        return None
    vectors = np.array([unit_vector(estimate) for estimate in estimates])
    agrees = vectors @ vectors.T >= math.cos(math.radians(25))
    votes = agrees.sum(axis=1)
    best = int(votes.argmax())
    if votes[best] < max(2, .60 * len(vectors)):
        return None
    x, y, z = vectors[agrees[best]].mean(axis=0)
    elevation = math.degrees(math.atan2(z, math.hypot(x, y)))
    return {
        "azimuth": math.degrees(math.atan2(y, x)) % 360 if abs(elevation) < 88 else None,
        "elevation": elevation if estimates[best]["elevation"] is not None else None,
    }


def suppress_noise(signal, profile):
    """Reduce persistent frequencies with a floor to retain weak consonants."""
    hop = FRAME // 2
    padded = np.pad(signal, (hop, hop + (-len(signal) % hop)))
    frames = np.lib.stride_tricks.sliding_window_view(padded, FRAME)[::hop]
    spectrum = np.fft.rfft(frames * TAPER, axis=1)
    power = np.abs(spectrum) ** 2
    gain = np.sqrt(np.clip(1. - 1.6 * profile / np.maximum(power, 1.), .04, 1.))
    restored = np.fft.irfft(spectrum * gain, n=FRAME, axis=1) * TAPER
    mixed, weights = np.zeros_like(padded), np.zeros_like(padded)
    for start, frame in enumerate(restored):
        section = slice(start * hop, start * hop + FRAME)
        mixed[section] += frame
        weights[section] += TAPER ** 2
    return (mixed / np.maximum(weights, 1e-8))[hop:hop + len(signal)]


def speech_audio(audio, positions, direction, noise):
    samples = audio.astype(float)
    samples -= samples.mean(axis=0)
    strength = np.mean(samples ** 2, axis=0)
    if noise is not None:
        strength /= np.maximum(1., noise[:, 10:135].sum(axis=1))
    channel = int(strength.argmax())
    raw = samples[:, channel]
    if noise is None:
        return raw, raw
    if direction is None:
        return raw, suppress_noise(raw, noise[channel])

    # Combine one board's four time-aligned mics. Its internal timing needs no
    # cross-board offset and the direction comes from the original eight channels.
    board = slice(channel // 4 * 4, channel // 4 * 4 + 4)
    microphones = positions[board] - positions[board].mean(axis=0)
    advance = (microphones @ unit_vector(direction)) * (RATE / 343.)
    time = np.arange(len(samples))
    aligned = [np.interp(time - delta, time, signal)
               for delta, signal in zip(advance, samples[:, board].T)]
    return raw, suppress_noise(np.mean(aligned, axis=0), noise[board].mean(axis=0) / 2.)


class SpeechTrigger:
    def __init__(self, recognizer, positions):
        self.recognizer = recognizer
        self.positions = positions
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="keyword")
        self.pending = None
        self.pause()

    def pause(self):
        self.clear_phrase()
        self.previous_end = None
        self.use_result = False

    def clear_phrase(self):
        self.preroll = deque(maxlen=2)
        self.chunks = []
        self.estimates = []
        self.samples = self.quiet = 0

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def recognize(self, audio, direction, noise):
        raw, cleaned = speech_audio(audio, self.positions, direction, noise)
        matched = self.recognizer.matches(cleaned) or (cleaned is not raw and self.recognizer.matches(raw))
        return (matched, direction if matched else None)

    def submit_phrase(self, noise):
        if self.chunks and self.pending is None:
            phrase = np.concatenate(self.chunks)
            self.pending = self.executor.submit(self.recognize, phrase, stable_direction(self.estimates), noise)
            self.use_result = True

    def process(self, audio, origin, active, estimate, noise):
        end = origin + len(audio)
        previous_end = self.previous_end
        result = None
        if self.pending is not None and self.pending.done():
            completed = self.pending
            self.pending = None
            if self.use_result:
                result = completed.result()

        if previous_end is None or end <= previous_end or origin - previous_end > RATE // 20:
            # Keep the result of any complete utterance already in the decoder.
            if previous_end is not None and origin > previous_end:
                self.submit_phrase(noise)
            self.clear_phrase()
            fresh = audio
        elif origin <= previous_end:
            fresh = audio[previous_end - origin:]
        else:
            fresh = np.pad(audio, ((origin - previous_end, 0), (0, 0)))
        self.previous_end = end

        if active:
            if not self.chunks:
                self.chunks.extend(self.preroll)
                self.samples = sum(map(len, self.chunks))
            self.quiet = 0
            if estimate is not None and (estimate["azimuth"] is not None or estimate["elevation"] is not None):
                self.estimates.append(estimate)
        elif not self.chunks:
            self.preroll.append(fresh)
            return result
        else:
            self.quiet += len(fresh)

        self.chunks.append(fresh)
        self.samples += len(fresh)
        if self.quiet >= 2 * HOP or self.samples >= 2 * RATE:
            self.submit_phrase(noise)
            self.chunks.clear()
            self.estimates.clear()
            self.preroll.clear()
            self.preroll.append(fresh)
            self.samples = self.quiet = 0
        return result
