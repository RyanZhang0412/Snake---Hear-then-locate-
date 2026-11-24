"""Eight-channel direction estimation in USB-referenced array coordinates."""
from itertools import combinations
import json
from pathlib import Path

import numpy as np

RATE = 16000
WINDOW = 4096
HOP = 2048
SLOT_WINDOW = 3 * WINDOW + 2
SLOT_HOP = 3 * HOP


def unpack_window(stereo):
    """Return the packed channels and their first I2S-frame index."""
    marks = stereo & 1
    for start in range(3):
        group = stereo[start:start + 3 * WINDOW].reshape(WINDOW, 3, 2)
        marker_group = marks[start:start + 3 * WINDOW].reshape(WINDOW, 3, 2)
        if (np.all(marker_group == np.array([[0], [1], [1]]))
                and np.all(group[:, 1:, 1] == 1)):
            return (np.column_stack((group[:, :, 0], group[:, 0, 1]))
                    & np.int32(-2)), start
    return None


class Localizer:
    frame = 512
    oversample = 8
    score_floor = .24

    def __init__(self, path: Path):
        config = json.loads(path.read_text(encoding="utf-8"))
        gap = float(config["board_gap_cm"])
        side = float(config["mic_square_side_mm"])
        rotation = float(config.get("lower_clockwise_rotation_deg", 0))
        if not (0 < gap <= 15 and 0 < side <= 150 and np.isfinite(rotation)):
            raise ValueError("请检查 array.json 的板间距、麦克风边长和旋转角")
        self.gap_cm = gap
        self.positions = np.empty((8, 3))
        for board, prefix in enumerate(("upper", "lower")):
            order = config[prefix + "_clockwise_channels"]
            if sorted(order) != [0, 1, 2, 3]:
                raise ValueError(f"{prefix} 的通道顺序必须包含 0、1、2、3 各一次")
            angles = np.deg2rad(np.arange(45, 360, 90) + (rotation if board else 0))
            xy = np.column_stack((np.cos(angles), np.sin(angles))) * side / (1000 * np.sqrt(2))
            self.positions[4 * board + np.asarray(order), :2] = xy
            self.positions[4 * board:4 * board + 4, 2] = (1 - 2 * board) * gap / 200
        self.pairs = np.array(list(combinations(range(8), 2)))
        self.cross = (self.pairs[:, 0] < 4) & (self.pairs[:, 1] >= 4)
        self.same = ~self.cross
        self.calibration_pairs = np.flatnonzero(self.cross & (np.linalg.norm(
            self.positions[self.pairs[:, 0], :2] - self.positions[self.pairs[:, 1], :2],
            axis=1) < .002))
        self.azimuth, self.elevation = np.meshgrid(
            np.arange(0., 360., 3.), np.arange(-90., 91., 3.))
        az, el = np.deg2rad(self.azimuth.ravel()), np.deg2rad(self.elevation.ravel())
        directions = np.array((np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)))
        self.directions = directions
        delta = self.positions[self.pairs[:, 0]] - self.positions[self.pairs[:, 1]]
        self.delays = (delta @ directions) * (RATE / 343.)
        self.fft_size = self.frame * self.oversample
        self.band = (np.fft.rfftfreq(self.frame, 1 / RATE) >= 450) & (
            np.fft.rfftfreq(self.frame, 1 / RATE) <= 3800)
        self.taper = np.hanning(self.frame)
        # ClapMonitor.direct_arrival places onset at sample 144. Favor the first
        # few milliseconds, with the same noise-power scale as the full window.
        self.clap_taper = np.zeros(self.frame)
        self.clap_taper[32:288] = np.hanning(256)
        self.clap_taper *= np.linalg.norm(self.taper) / np.linalg.norm(self.clap_taper)

    def measure(self, audio, noise=None, *, taper=None):
        x = audio.astype(float).T
        frames = np.lib.stride_tricks.sliding_window_view(x, self.frame, axis=-1)[:, ::256]
        spectrum = np.fft.rfft(frames * (self.taper if taper is None else taper), axis=-1)
        power = np.abs(spectrum) ** 2
        band_power = power[:, :, self.band].sum(axis=-1).mean(axis=1)
        levels = 10 * np.log10(np.maximum(np.median(band_power.reshape(2, 4), axis=1), 1.))
        profile = np.median(power, axis=1)
        if np.any(np.std(x, axis=1) < 2000):
            return None, levels, profile
        i, j = self.pairs.T
        cross = spectrum[i].conj() * spectrum[j]
        phat = cross / np.maximum(np.abs(cross), 1e-12)
        cross = phat.mean(axis=1)
        if noise is not None:
            # Retain phase where both mics rise above the learned room spectrum.
            excess = np.maximum(0., 1. - 1.35 * noise[:, None, :] / np.maximum(power, 1.))
            weight = np.sqrt(excess[i] * excess[j])
            # Cap the compensation so a few harmonics cannot create a strong match.
            support = np.maximum(weight[:, :, self.band].mean(axis=(1, 2)), .42)
            weighted = (phat * weight).mean(axis=1) / support[:, None]
            cross = .20 * cross + .80 * weighted
        cross[:, ~self.band] = 0
        corr = np.fft.irfft(cross, n=self.fft_size, axis=-1)
        corr = np.fft.fftshift(corr, axes=-1) * (self.fft_size / (2 * self.band.sum()))
        return corr, levels, profile

    def correlations(self, audio, noise=None):
        return self.measure(audio, noise)[0]

    def clap_direction(self, audio, phase, offset, noise=None):
        correlations, _, _ = self.measure(audio, noise, taper=self.clap_taper)
        if correlations is None:
            return None
        estimate = self.direction(correlations, phase, offset)
        if estimate["azimuth"] is not None or estimate["elevation"] is not None:
            return estimate
        return None

    def reference_offset(self, correlations, phase):
        """At the middle plane, vertically matched microphones are equidistant."""
        maximum = 14 * self.oversample
        center = self.fft_size // 2
        votes = []
        for row in correlations[self.calibration_pairs]:
            band = row[center - maximum:center + maximum + 1]
            peak = int(np.argmax(band))
            if not 0 < peak < len(band) - 1 or band[peak] < .32:
                continue
            distant = np.abs(np.arange(len(band)) - peak) >= 3 * self.oversample
            if np.any(band[distant] > .88 * band[peak]):
                continue
            left, top, right = band[peak - 1:peak + 2]
            curvature = left - 2 * top + right
            fraction = .5 * (left - right) / curvature if curvature else 0.
            votes.append((peak + fraction - maximum) / self.oversample + phase)
        if len(votes) >= 3:
            middle = float(np.median(votes))
            if sum(abs(vote - middle) <= .35 for vote in votes) >= 3:
                return middle
        return None

    def direction(self, correlations, phase, offset):
        if offset is None:
            mask = self.same
            indices = self.elevation.ravel() >= 0
            corrections = np.zeros(mask.sum())
        else:
            mask = np.ones(len(self.pairs), dtype=bool)
            indices = slice(None)
            corrections = self.cross * (offset - phase)
        lags = (self.delays[mask][:, indices] + corrections[:, None]) * self.oversample
        lags += self.fft_size // 2
        floors = lags.astype(np.int32)
        frac = lags - floors
        cc = correlations[mask]
        # Adjacent interpolated GCC values let the sphere grid use fractional delays.
        strengths = (1 - frac) * np.take_along_axis(cc, floors, axis=1)
        strengths += frac * np.take_along_axis(cc, floors + 1, axis=1)
        if offset is None:
            scores = strengths.mean(axis=0)
        else:
            # Give the in-plane and cross-plane evidence equal aggregate weight.
            scores = .5 * strengths[self.same].mean(axis=0) + .5 * strengths[self.cross].mean(axis=0)
        winner = int(np.argmax(scores))
        result = {"azimuth": None, "elevation": None, "score": round(float(scores[winner]), 3)}
        if scores[winner] < self.score_floor:
            return result
        if offset is None:
            candidates = self.azimuth.ravel()[indices]
            separation = np.abs((candidates - candidates[winner] + 180) % 360 - 180)
            distant = separation >= 28
        else:
            directions = self.directions[:, indices]
            similarities = np.sum(directions * directions[:, winner, None], axis=0)
            distant = similarities < np.cos(np.deg2rad(28))
        if np.any(distant) and scores[winner] - scores[distant].max() < .018:
            return result
        elevation = float(self.elevation.ravel()[indices][winner])
        if abs(elevation) < 90:
            result["azimuth"] = float(self.azimuth.ravel()[indices][winner])
        if offset is not None:
            result["elevation"] = elevation
        return result
