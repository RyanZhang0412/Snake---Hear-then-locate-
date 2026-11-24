import unittest

import numpy as np

from stream import HEADER, datagram_records


class WifiFormatTests(unittest.TestCase):
    def test_three_bundled_records_restore_phase_channels_and_scale(self):
        datagram = bytearray(b"X16B")
        expected = []
        for first, shift in enumerate((0, 7, 16)):
            words = bytearray((first, shift))
            block = []
            for i in range(128):
                mark = int(i % 3 != first)
                left = -32768 + i * 2
                right = 32766 - i * 2
                pair = [left] if mark else [left, right]
                words.extend(np.array(pair, dtype="<i2").tobytes())
                block.append([(left << shift) | mark, 1 if mark else right << shift])
            board = first % 2
            datagram.extend(HEADER.pack(b"XVF2" if board else b"XVF1", first, 128 * first,
                                        128, len(words), 12345, 0))
            datagram.extend(words)
            expected.append(block)
        for i, (board, (start, count, seq, stamp, overflows, audio)) in enumerate(datagram_records(bytes(datagram))):
            self.assertEqual((board, start, count, seq, stamp, overflows), (i % 2, 128 * i, 128, i, 12345, 0))
            np.testing.assert_array_equal(audio, expected[i])
        for malformed in (b"X16B", datagram[:-1], datagram + b"x"):
            with self.assertRaises(ValueError):
                datagram_records(malformed)
