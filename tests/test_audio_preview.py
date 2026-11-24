import base64
from concurrent.futures import Future
from io import BytesIO
import unittest
import wave

import numpy as np

from localize import RATE, WINDOW
from mic_preview import RecentAudio, preview_pair
from speech_events import SpeechTrigger


class AudioPreviewTests(unittest.TestCase):
    def test_buffer_removes_overlap_and_keeps_missing_samples_audible_as_silence(self):
        audio = RecentAudio()
        audio.append(0, np.full((100, 8), 1, dtype=np.int32))
        audio.append(80, np.full((100, 8), 2, dtype=np.int32))
        audio.append(200, np.full((100, 8), 3, dtype=np.int32))
        clip, missing = audio.snapshot()
        self.assertEqual(missing, 20)
        self.assertEqual(list(clip[[99, 100, 179, 180, 199, 200], 0]), [1, 2, 2, 0, 0, 3])

    def test_preview_is_bounded_and_both_versions_are_playable(self):
        audio = RecentAudio()
        values = np.random.default_rng(5).integers(-200000, 200000, (RATE * 5, 8), dtype=np.int32)
        audio.append(0, values)
        clip, missing = audio.snapshot()
        result = preview_pair(clip, missing, np.zeros((8, 3)), None, np.full((8, 257), 1e12))
        self.assertEqual(result["duration"], 4)
        self.assertNotEqual(result["raw"], result["cleaned"])
        for name in ("raw", "cleaned"):
            with wave.open(BytesIO(base64.b64decode(result[name])), "rb") as track:
                self.assertEqual((track.getnchannels(), track.getsampwidth(), track.getframerate()), (1, 2, RATE))
                self.assertEqual(track.getnframes(), RATE * 4)

    def test_large_gap_does_not_discard_submitted_keyword_result(self):
        detector = SpeechTrigger(None, np.zeros((8, 3)))
        self.addCleanup(detector.close)
        detector.pending = Future()
        detector.use_result = True
        detector.previous_end = WINDOW
        direction = {"azimuth": 40., "elevation": None}
        detector.pending.set_result((True, direction))
        recovered = detector.process(np.zeros((WINDOW, 8), dtype=np.int32), WINDOW + RATE // 2,
                                     False, None, None)
        self.assertEqual(recovered, (True, direction))
