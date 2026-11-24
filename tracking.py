"""Reject background sound and require a repeatable direction before display."""
from collections import deque
import math

import numpy as np


class SoundActivity:
    def __init__(self):
        self.start = []
        self.floor = None

    def update(self, levels, *, learn=True, continuing=False, sensitive=False):
        if self.floor is None:
            if learn:
                self.start.append(levels.copy())
                if len(self.start) >= 8:
                    self.floor = np.percentile(self.start, 30, axis=0)
                    self.start.clear()
            return "learning"
        rise = levels - self.floor
        threshold = (2.25 if continuing else 3.) if sensitive else (3. if continuing else 4.5)
        active = bool(rise.min() >= threshold - 1. and rise.mean() >= threshold)
        if learn and not active:
            change = np.clip(rise * .05, -.8, .04)
            self.floor += change
        return "active" if active else "quiet"


def unit_vector(estimate):
    azimuth = math.radians(estimate["azimuth"] or 0.)
    elevation = math.radians(estimate["elevation"] or 0.)
    horizontal = math.cos(elevation)
    return np.array((horizontal * math.cos(azimuth), horizontal * math.sin(azimuth),
                     math.sin(elevation)))


class DirectionTracker:
    expiry = .68
    agreement = math.cos(math.radians(17))
    switching = math.cos(math.radians(29))

    def __init__(self):
        self.vector = None
        self.candidates = deque(maxlen=5)
        self.last = -float("inf")

    def update(self, estimate, now):
        if now - self.last >= self.expiry:
            self.vector = None
        if estimate is None or (estimate["azimuth"] is None and estimate["elevation"] is None):
            self.candidates.append(None)
            return None
        current = unit_vector(estimate)
        self.candidates.append(current)
        group = [v for v in self.candidates if v is not None and v @ current >= self.agreement]
        # A new or substantially different direction requires three agreeing windows.
        if len(group) < 3:
            return None
        average = np.mean(group, axis=0)
        average /= np.linalg.norm(average)
        if self.vector is None or self.vector @ average < self.switching:
            self.vector = average
        else:
            self.vector = .68 * self.vector + .32 * average
            self.vector /= np.linalg.norm(self.vector)
        self.last = now
        x, y, z = self.vector
        elevation = math.degrees(math.atan2(z, math.hypot(x, y)))
        return {
            "azimuth": math.degrees(math.atan2(y, x)) % 360 if abs(elevation) < 88 else None,
            "elevation": elevation if estimate["elevation"] is not None else None,
            "score": estimate["score"],
        }
