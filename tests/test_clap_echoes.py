from pathlib import Path
import unittest

import numpy as np

from app import Engine
from localize import Localizer, RATE
from sound_events import ClapMonitor
from spatial_claps import SpatialClaps


class AcceptClaps:
    def score(self, history, onset):
        return .8, False


class UncertainClaps:
    def score(self, history, onset):
        return .04, False


class WeakClapMonitor(ClapMonitor):
    @staticmethod
    def resembles_isolated_clap(impulse, background, sensitivity):
        return False


class ClapEchoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = Localizer(Path(__file__).resolve().parents[1] / "array.json")
        cls.positions = cls.model.positions
        cls.estimator = SpatialClaps(cls.positions)

    def setUp(self):
        self.rng = np.random.default_rng(42)

    def pulse(self, onset, size=512):
        result = np.zeros(size)
        result[onset:onset + 90] = self.rng.normal(size=90) * np.exp(-np.arange(90) / 28)
        return result

    def arrival(self, pulse, azimuth, elevation, distance=None):
        az, el = np.deg2rad([azimuth, elevation])
        direction = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
        if distance is None:
            path = -(self.positions @ direction)
        else:
            path = np.linalg.norm(self.positions - distance * direction, axis=1) - distance
        frequency = np.fft.rfftfreq(len(pulse), 1 / RATE)
        delays = np.exp(-2j * np.pi * frequency[:, None] * path / 343.)
        return np.fft.irfft(np.fft.rfft(pulse)[:, None] * delays, axis=0) * 1e7

    def live_events(self, audio, monitor, sensitivity):
        engine = Engine("demo", self.model)
        engine.monitor = monitor
        engine.offset = 0.
        engine.set_audio("sensitivity", sensitivity)
        engine.game_input(0)
        for start in range(0, len(audio) - 4095, 2048):
            engine.on_window((audio[start:start + 4096].astype(np.int32), 0., start))
        return engine.game_input(0)["events"]

    def test_louder_reflection_keeps_the_direct_direction(self):
        pulse = self.pulse(140)
        direct = self.arrival(pulse, 27, 24, .5)
        direct *= [.75, 1.1, .7, .85, 1.15, .73, 1.25, .8]
        reflected = self.arrival(np.roll(pulse, 72), 155, -18) * 1.3
        groups = self.estimator.estimate(direct + reflected, 0., 0.)
        self.assertEqual(len(groups), 1)
        self.assertAlmostEqual(groups[0]["azimuth"], 27, delta=10)
        self.assertAlmostEqual(groups[0]["elevation"], 24, delta=10)

    def test_reflection_is_also_removed_before_elevation_calibration(self):
        pulse = self.pulse(140)
        audio = self.arrival(pulse, 27, 24, .6) + .65 * self.arrival(np.roll(pulse, 80), 155, -18)
        groups = self.estimator.estimate(audio, 0., None)
        self.assertEqual(len(groups), 1)
        self.assertAlmostEqual(groups[0]["azimuth"], 27, delta=12)
        self.assertIsNone(groups[0]["elevation"])

    def test_independent_overlapping_claps_remain_separate(self):
        angles = [(30, 20), (125, -25), (230, 35), (320, -25)]
        signals = [self.arrival(self.pulse(136 + i * 3), *angle) for i, angle in enumerate(angles)]
        for count in (2, 4):
            groups = self.estimator.estimate(sum(signals[:count]), 0., 0.)
            self.assertEqual(len(groups), count)
            for azimuth, _ in angles[:count]:
                self.assertTrue(any(abs(group["azimuth"] - azimuth) <= 15 for group in groups))

    def test_late_echo_does_not_fire_but_a_new_clap_does(self):
        pulse = self.pulse(1952, size=12288)
        audio = self.arrival(pulse, 20, 0)
        audio += .65 * self.arrival(np.roll(pulse, 2000), 155, 0)
        audio += .8 * self.arrival(self.pulse(4608, size=12288), 260, 0)
        audio += self.rng.normal(size=audio.shape) * 1e4
        monitor = ClapMonitor(AcceptClaps())
        events = []
        for start in range(0, len(audio) - 4095, 2048):
            events.extend(monitor.process(audio[start:start + 4096], start, "standard", collect_game=True))
        game = [sample for kind, _, _, sample in events if kind == "game"]
        self.assertEqual(len(game), 2)
        self.assertAlmostEqual(game[0], 1952, delta=96)
        self.assertAlmostEqual(game[1], 4608, delta=96)

    def test_one_onset_does_not_publish_multiple_peak_directions(self):
        audio = self.arrival(self.pulse(1952, 12288), 30, 21)
        for delay, azimuth in [(36, 125), (94, 230), (126, 320)]:
            audio += .6 * self.arrival(self.pulse(1952 + delay, 12288), azimuth, 0)
        audio += self.rng.normal(size=audio.shape) * 1e4
        events = self.live_events(audio, ClapMonitor(AcceptClaps()), "standard")
        self.assertEqual([len(event["groups"]) for event in events], [1])
        self.assertAlmostEqual(events[0]["groups"][0]["azimuth"], 30, delta=15)

    def test_weak_localized_clap_survives_classifier_uncertainty(self):
        pulse = self.arrival(self.pulse(1952, 12288), 60, 22)
        noise = np.random.default_rng(77).normal(size=pulse.shape) * 5e5
        audio = .18 * pulse + noise
        candidate = ClapMonitor.candidate_blocks(audio[:4096], "high")
        self.assertTrue(any(onset == 1952 and 5.5 <= rise < 9. for onset, rise in candidate))
        events = self.live_events(audio, WeakClapMonitor(UncertainClaps()), "high")
        self.assertEqual([len(event["groups"]) for event in events], [1])
        self.assertAlmostEqual(events[0]["groups"][0]["azimuth"], 60, delta=12)
        self.assertFalse(self.live_events(noise, WeakClapMonitor(UncertainClaps()), "high"))

    def test_four_distinct_onsets_can_publish_during_one_round(self):
        angles = [30, 120, 210, 300]
        audio = sum(self.arrival(self.pulse(1952 + i * 1280, 12288), angle, 18)
                    for i, angle in enumerate(angles))
        audio += self.rng.normal(size=audio.shape) * 1e4
        events = self.live_events(audio, ClapMonitor(AcceptClaps()), "standard")
        self.assertEqual([len(event["groups"]) for event in events], [1, 1, 1, 1])
        self.assertLess(events[-1]["at"] - events[0]["at"], .34)
        for event, angle in zip(events, angles):
            self.assertAlmostEqual(event["groups"][0]["azimuth"], angle, delta=12)
