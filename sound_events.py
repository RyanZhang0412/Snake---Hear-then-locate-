"""PCM level measurement and local, onset-gated clap classification."""
from collections import deque
from pathlib import Path
import zipfile

import numpy as np

from localize import RATE

FULL_SCALE = 2 ** 31
BLOCK = 64
MODEL_SAMPLES = 15600
MODEL_TAIL = int(.14 * RATE)
REFRACTORY = int(.23 * RATE)
ECHO_TAIL = int(.20 * RATE)


def matching_impulse(reference, candidate, max_shift):
    """Return the sample lag only when the broadband waveforms closely match."""
    scale = np.linalg.norm(reference) * np.linalg.norm(candidate)
    if not scale:
        return None
    correlation = np.abs(np.correlate(candidate, reference, mode="full"))
    center = len(reference) - 1
    lo = max(0, center - max_shift)
    hi = min(len(correlation), center + max_shift + 1)
    index = lo + int(np.argmax(correlation[lo:hi]))
    return index - center if correlation[index] >= .70 * scale else None


def pcm_levels(audio):
    """Peak recent 16 ms RMS and typical quiet-block RMS, in dBFS."""
    blocks = audio.astype(float).reshape(-1, 256, 8)
    blocks -= blocks.mean(axis=1, keepdims=True)
    energy = np.median(np.mean(blocks * blocks, axis=1), axis=1)
    levels = 10 * np.log10(np.maximum(energy / FULL_SCALE ** 2, 1e-10))
    return float(levels[-8:].max()), float(np.percentile(levels, 25))


class Yamnet:
    def __init__(self, model_path: Path):
        from ai_edge_litert.interpreter import Interpreter

        with zipfile.ZipFile(model_path) as archive:
            labels = archive.read("yamnet_label_list.txt").decode("utf-8").splitlines()
        self.clapping = labels.index("Clapping")
        self.applause = labels.index("Applause")
        self.typing = [labels.index(name) for name in ("Typing", "Computer keyboard", "Typewriter")]
        self.confusers = [labels.index(name) for name in ("Cough", "Knock", "Finger snapping")]
        self.speech = labels.index("Speech")
        self.model = Interpreter(model_path=str(model_path), num_threads=1)
        self.model.allocate_tensors()
        self.input = self.model.get_input_details()[0]["index"]
        self.output = self.model.get_output_details()[0]["index"]

    def score(self, history, onset):
        left = max(0, onset + MODEL_TAIL - MODEL_SAMPLES)
        clip = history[left:onset + MODEL_TAIL].astype(np.float32)
        if len(clip) == 0:
            return 0., False
        transient = history[max(0, onset - 64):onset + 320].astype(float)
        scores = []
        for start in (0, 4):
            channel = start + int(np.argmax(np.std(transient[:, start:start + 4], axis=0)))
            signal = clip[:, channel].copy()
            signal -= signal.mean()
            signal *= .85 / max(float(np.max(np.abs(signal))), 1.)
            padded = np.zeros(MODEL_SAMPLES, dtype=np.float32)
            padded[-len(signal):] = signal
            self.model.set_tensor(self.input, padded)
            self.model.invoke()
            scores.append(self.model.get_tensor(self.output).ravel())
        score = np.mean(scores, axis=0)
        clap = float(max(score[self.clapping], score[self.applause]))
        blocked = (score[self.typing].max() > max(.065, clap)
                   or score[self.confusers].max() > max(.28, clap)
                   or score[self.speech] > max(.55, 2 * clap))
        return clap, bool(blocked)


class ClapMonitor:
    def __init__(self, model):
        self.model = model
        self.audio = np.empty((0, 8), dtype=np.int32)
        self.start = None
        self.pending = []
        self.last_clap = None
        self.last_preview = None
        self.examined = None
        self.recent_claps = deque(maxlen=4)

    def _append(self, audio, start):
        if self.start is None or start < self.start or start > self.start + len(self.audio):
            self.start = start
            self.audio = audio.copy()
            self.pending.clear()
            self.examined = None
            self.recent_claps.clear()
            return
        count = self.start + len(self.audio) - start
        self.audio = np.concatenate((self.audio, audio[count:]))
        trim = max(0, len(self.audio) - MODEL_SAMPLES - len(audio))
        self.audio = self.audio[trim:]
        self.start += trim

    @staticmethod
    def candidate_blocks(audio, sensitivity):
        chunks = audio.astype(float).reshape(-1, BLOCK, 8)
        chunks -= chunks.mean(axis=1, keepdims=True)
        power = np.median(np.mean(chunks * chunks, axis=1).reshape(-1, 2, 4), axis=2)
        spectrum = np.abs(np.fft.rfft(chunks * np.hanning(BLOCK)[None, :, None], axis=1)) ** 2
        # 750–5000 Hz isolates the snap from rumble and slow changes in level.
        mid_power = np.median(spectrum[:, 3:21].sum(axis=1).reshape(-1, 2, 4), axis=2)
        thresholds = ((6., 3.75, 3.), (5.5, 3.5, 3.)) if sensitivity == "high" else (
            (9., 5.5, 4.5), (8., 5., 4.))
        events = {}
        for energy, (lift, attack, decay) in zip((power, mid_power), thresholds):
            levels = 10 * np.log10(np.maximum(energy, 1))
            combined = levels.mean(axis=1)
            for peak in range(16, len(levels) - 14):
                if combined[peak] < max(combined[peak - 1], combined[peak + 1]):
                    continue
                before = np.median(levels[peak - 16:peak - 4], axis=0)
                immediately_before = np.median(levels[peak - 4:peak], axis=0)
                after = np.median(levels[peak + 6:peak + 14], axis=0)
                hit = levels[peak]
                rise = float(min(hit - before))
                if rise >= lift and min(hit - immediately_before) >= attack and min(hit - after) >= decay:
                    onset = peak * BLOCK + BLOCK // 2
                    events[onset] = max(rise, events.get(onset, 0.))
        return sorted(events.items())

    @staticmethod
    def resembles_isolated_clap(impulse, background, sensitivity):
        """Some single claps get low scores on a model trained on longer scenes."""
        x = impulse.astype(float)
        x -= x.mean(axis=0)
        taper = np.hanning(len(x))[:, None]
        power = np.abs(np.fft.rfft(x * taper, axis=0)) ** 2
        if len(background) == len(x):
            quiet = background.astype(float)
            quiet -= quiet.mean(axis=0)
            noise = np.abs(np.fft.rfft(quiet * taper, axis=0)) ** 2
            power = np.maximum(power - noise, 0.)
        frequency = np.fft.rfftfreq(len(x), 1 / RATE)
        total = np.maximum(power.sum(axis=0), 1.)
        low = np.median((power[frequency < 500].sum(axis=0) / total).reshape(2, 4), axis=1)
        middle = np.median((power[(frequency >= 750) & (frequency < 3650)].sum(axis=0)
                            / total).reshape(2, 4), axis=1)
        high = np.median((power[frequency >= 5000].sum(axis=0) / total).reshape(2, 4), axis=1)
        low_limit, middle_min = (.26, .40) if sensitivity == "high" else (.17, .45)
        return low.max() < low_limit and middle.min() > middle_min and high.max() < .48

    @staticmethod
    def direct_arrival(audio, peak):
        # Backtrack from the brightest block to the first rise present on both boards.
        start = max(0, peak - 256)
        chunks = audio[start:peak + 64].astype(float).reshape(-1, 32, 8)
        chunks -= chunks.mean(axis=1, keepdims=True)
        power = np.median(np.mean(chunks ** 2, axis=1).reshape(-1, 2, 4), axis=2)
        # The relative peak threshold alone can be below ambient when the clap is soft.
        threshold = np.maximum(.18 * power.max(axis=0), 1.8 * np.median(power, axis=0))
        early = np.flatnonzero(np.all(power >= threshold, axis=1))
        onset = start + 32 * int(early[0]) if len(early) else peak
        left = max(0, onset - 144)
        return audio[left:left + 512]

    def repeated_impulse(self, impulse, sample):
        # Late echoes can trigger a new onset. Compare the same microphones across
        # the two arrivals and require a waveform match on each independent board.
        spectrum = np.fft.rfft(impulse, axis=0)
        frequency = np.fft.rfftfreq(len(impulse), 1 / RATE)
        spectrum[(frequency < 1000) | (frequency > 6500)] = 0
        filtered = np.fft.irfft(spectrum, n=len(impulse), axis=0).T
        while self.recent_claps and sample - self.recent_claps[0][0] > ECHO_TAIL:
            self.recent_claps.popleft()
        for _, reference in self.recent_claps:
            if all(any(matching_impulse(reference[i], filtered[i], 96) is not None
                       for i in range(first, first + 4)) for first in (0, 4)):
                return True
        self.recent_claps.append((sample, filtered))
        return False

    def process(self, audio, start, sensitivity, *, collect_game=False):
        self._append(audio, start)
        events = []
        for onset, rise in self.candidate_blocks(audio, sensitivity):
            candidate = start + onset
            if self.examined is not None and candidate <= self.examined:
                continue
            if self.pending and candidate <= self.pending[-1][0]:
                continue
            if self.pending and candidate - self.pending[-1][0] < 480:
                if rise > self.pending[-1][1]:
                    self.pending[-1] = (candidate, rise)
            else:
                self.pending.append((candidate, rise))
            if sensitivity == "high" and (self.last_preview is None
                                          or candidate - self.last_preview >= REFRACTORY):
                index = candidate - self.start
                self.last_preview = candidate
                events.append(("transient", self.direct_arrival(self.audio, index), rise, candidate))

        while self.pending and self.pending[0][0] + MODEL_TAIL <= self.start + len(self.audio):
            candidate, rise = self.pending.pop(0)
            self.examined = candidate
            display = self.last_clap is None or candidate - self.last_clap >= REFRACTORY
            if not display and not collect_game:
                continue
            index = candidate - self.start
            # The short direct arrival is more useful for direction than the full classifier context.
            lo = max(0, index - 168)
            score, blocked = self.model.score(self.audio, index)
            threshold = .085 if sensitivity == "high" else .17
            if blocked:
                continue
            if score >= threshold:
                kind = "confirmed"
            elif self.resembles_isolated_clap(self.audio[lo:lo + 512],
                                             self.audio[max(0, lo - 1152):max(0, lo - 640)],
                                             sensitivity):
                kind = "possible"
            else:
                kind = None
            # For weak, unvetoed attacks in high mode, let the array's timing
            # agreement decide. The caller uses a stricter score for these.
            accept_game = collect_game and (kind is not None or sensitivity == "high" and rise >= 5.5)
            if kind is None and not accept_game:
                continue
            arrival = self.direct_arrival(self.audio, index)
            if self.repeated_impulse(arrival, candidate):
                continue
            if display and kind is not None:
                self.last_clap = candidate
                events.append((kind, arrival, rise, candidate))
            if accept_game:
                events.append(("game" if kind else "weak_game", arrival, rise, candidate))
        return events
