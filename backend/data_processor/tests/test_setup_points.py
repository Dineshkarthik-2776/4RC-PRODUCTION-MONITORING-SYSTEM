"""B_point (SETUP START) and C_point (RAMP UP) signal detection.

Documentation section 3.3 defines these points:

  Point B -- "at least two consecutive sustained speed readings (LS >= 10
             m/min) that are definitively followed by a reading of 0 m/min.
             (As a secondary fallback ... a 15-sample rolling window of
             LS >= 4 leading to a zero)."

  Point C -- "a rolling window of 15 consecutive readings where the speed is
             consistently >= 4 m/min, marking the start of that window as
             Point C."

Every changeover duration in the system derives from these two indices, so the
cases below pin both the happy paths and the not-found paths.
"""

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from data_processor.tasks import B_point, C_point


def signal(speeds):
    """Build the flag columns the detectors read from a list of line speeds."""
    s = pd.Series(speeds, dtype=float)
    return pd.DataFrame({
        'Line_Speed_Act': s,
        'LS DROP': np.where(s == 0, 1, 0),
        'LS>=10': np.where(s >= 10, 1, 0),
        'LS>=4': np.where(s >= 4, 1, 0),
    })


class BPointTests(SimpleTestCase):
    """B_point returns the index where the line reaches a standstill."""

    def test_sustained_high_speed_then_zero(self):
        # Two consecutive LS>=10 readings, decelerating, then zero at index 5.
        df = signal([12, 12, 12, 8, 5, 0, 0, 3])
        self.assertEqual(B_point(0, 7, df), 5)

    def test_returns_the_first_zero_not_the_last(self):
        df = signal([12, 12, 0, 0, 0, 0])
        self.assertEqual(B_point(0, 5, df), 2)

    def test_no_zero_reading_returns_nan(self):
        # The line never stopped, so there is no setup start to find.
        df = signal([5, 5, 5, 5, 5])
        self.assertTrue(pd.isna(B_point(0, 4, df)))

    def test_empty_slice_returns_nan(self):
        df = signal([5, 5, 5])
        self.assertTrue(pd.isna(B_point(10, 3, df)))

    def test_long_sustained_low_speed_then_zero(self):
        # 20 readings at LS>=4 (but below 10), then a stop: the documented
        # secondary fallback path.
        df = signal([6] * 20 + [0, 0])
        self.assertEqual(B_point(0, 21, df), 20)

    def test_single_high_reading_still_resolves(self):
        # DOC MISMATCH.
        # The PDF requires "at least two consecutive" LS>=10 readings before a
        # zero. Here only index 0 is >=10, yet the function still returns the
        # first zero rather than NaN -- the non-sustained branch falls through
        # to the same minimum-drop lookup. Transient sensor noise can therefore
        # register as a setup start. Asserting current behaviour; the fix, if
        # the team wants one, belongs in the non-sustained branch of B_point.
        df = signal([12, 5, 3, 0, 0])
        self.assertEqual(B_point(0, 4, df), 3)


class CPointTests(SimpleTestCase):
    """C_point returns the index where sustained acceleration begins."""

    def test_window_start_is_returned_not_the_window_end(self):
        # Three zero readings, then 20 consecutive readings at LS>=4.
        # Acceleration begins at index 3, which is what the PDF calls Point C.
        df = signal([0, 0, 0] + [6] * 20)
        self.assertEqual(C_point(0, 22, df), 3)

    def test_empty_slice_returns_nan(self):
        df = signal([0, 0, 6])
        self.assertTrue(pd.isna(C_point(10, 3, df)))

    def test_batch_shorter_than_16_samples_never_resolves(self):
        # DOC MISMATCH / latent defect.
        #
        # C_point pads its working frame with 15 synthetic LS>=4 rows, intending
        # short batches to resolve. But the window check validates both window
        # edges against the ORIGINAL roll_batch index:
        #
        #     if start_point not in roll_batch.index or end_point not in roll_batch.index:
        #         continue
        #
        # The synthetic indices are absent from roll_batch, so every window is
        # skipped and the padding can never help. Any batch with fewer than 16
        # real samples after `first` therefore yields NaN -- no ramp-up point,
        # no setup time, and the row is written with a Remarks string instead.
        #
        # At the observed 10-second sampling rate that is any ramp-up under
        # ~2.5 minutes. Asserting current behaviour so the fix is visible when
        # someone makes it.
        df = signal([0, 0, 0, 6, 6])
        self.assertTrue(pd.isna(C_point(0, 4, df)))

    def test_exactly_16_samples_resolves(self):
        # The boundary of the behaviour pinned above.
        df = signal([0] + [6] * 16)
        self.assertEqual(C_point(0, 16, df), 1)

    def test_no_sustained_acceleration_returns_nan(self):
        # Speed twitches above 4 but never for 15 consecutive readings.
        df = signal([0, 6, 0, 6, 0, 6, 0, 6, 0, 6, 0, 6, 0, 6, 0, 6, 0, 6])
        self.assertTrue(pd.isna(C_point(0, 17, df)))

    def test_short_frame_branch_is_unreachable(self):
        # DOC MISMATCH / dead code.
        # C_point contains a `len(FirsttoD_df.index) < 16` branch, but the
        # padding above it always adds exactly 15 rows to a non-empty frame,
        # so the length is always >= 16 and that branch can never run. An empty
        # frame returns NaN before reaching it. Documented here so nobody
        # "fixes" short-batch handling by editing unreachable code.
        single_row = signal([6])
        self.assertTrue(pd.isna(C_point(0, 0, single_row)))
