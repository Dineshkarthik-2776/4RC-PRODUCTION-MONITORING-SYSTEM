"""calculate_time_loss: static setup, ramp-up and AUC-based time loss.

Documentation section 3.4 defines the metrics, mapping the four signal points
onto the column names used here:

    Static Setup Time      = Time(C) - Time(B)   = RAMP_UP   - SETUP_START
    Ramp Up Time           = Time(D) - Time(C)   = SETUP_COMPLETE - RAMP_UP
    Actual Total Setup     = Static Setup + Ramp Up Time

Time loss is the gap between elapsed time and the time the run would have taken
at target speed, derived from the area under the speed curve (section 3.4).

The function returns a 5-tuple:
    (ramp_up_loss, ramp_down_loss, total_setup_time, static_setup, ramp_up_time)
"""

import pandas as pd
from django.test import SimpleTestCase

from data_processor.tasks import calculate_time_loss

TZ = 'Asia/Kolkata'


def build_batch(aware=True, target_speed=10.0):
    """A clean changeover: 5 min stopped, then a linear ramp 0->10 over 10 min.

    Returns (roll2, batch_first) shaped as the pipeline passes them in.
    """
    index = pd.date_range(
        '2026-04-07 10:00:00', periods=21, freq='1min',
        tz=TZ if aware else None,
    )
    speeds = [0] * 5 + list(range(0, 11)) + [10] * 5
    roll2 = pd.DataFrame({
        'DATE TIME': index,
        'Line_Speed_Act': speeds[:21],
        'Target Speed': [target_speed] * 21,
    })
    batch_first = pd.DataFrame([{
        'BATCH': 1,
        'RAMP_DOWN': pd.Timestamp('2026-04-07 09:58:00'),
        'SETUP_START': pd.Timestamp('2026-04-07 10:00:00'),
        'RAMP_UP': pd.Timestamp('2026-04-07 10:05:00'),
        'SETUP_COMPLETE': pd.Timestamp('2026-04-07 10:15:00'),
    }])
    return roll2, batch_first


class TimeCalculationTests(SimpleTestCase):
    """A known signal produces hand-checkable numbers."""

    def setUp(self):
        self.roll2, self.batch_first = build_batch()
        self.result = calculate_time_loss(1, self.roll2, self.batch_first)

    def test_static_setup_is_ramp_up_minus_setup_start(self):
        # 10:00 -> 10:05
        self.assertEqual(self.result[3], 5.0)

    def test_ramp_up_time_is_setup_complete_minus_ramp_up(self):
        # 10:05 -> 10:15
        self.assertEqual(self.result[4], 10.0)

    def test_total_setup_is_static_plus_ramp_up(self):
        self.assertEqual(self.result[2], 15.0)
        self.assertEqual(self.result[2], self.result[3] + self.result[4])

    def test_ramp_up_time_loss_from_area_under_curve(self):
        # Speed climbs linearly 0 -> 10 over 10 minutes, so the area under the
        # curve is 50 unit-minutes. At the target speed of 10 the same output
        # needs 5 minutes, against 10 elapsed -- a loss of 5.
        self.assertEqual(self.result[0], 5.0)

    def test_ramp_down_loss_is_zero_when_line_already_stopped(self):
        # RAMP_DOWN 09:58 -> SETUP_START 10:00 contains only zero-speed rows.
        self.assertEqual(self.result[1], 0.0)


class DefensivePathTests(SimpleTestCase):
    """Missing or degenerate inputs return NaN rather than raising."""

    def test_unknown_batch_returns_all_nan(self):
        roll2, batch_first = build_batch()
        self.assertTrue(all(pd.isna(v) for v in calculate_time_loss(999, roll2, batch_first)))

    def test_zero_target_speed_does_not_divide_by_zero(self):
        roll2, batch_first = build_batch(target_speed=0.0)
        result = calculate_time_loss(1, roll2, batch_first)
        self.assertTrue(pd.isna(result[0]))   # ramp-up loss
        self.assertTrue(pd.isna(result[4]))   # ramp-up time
        self.assertEqual(result[3], 5.0)      # static setup needs no target speed

    def test_missing_target_speed_column_is_handled(self):
        roll2, batch_first = build_batch()
        result = calculate_time_loss(1, roll2.drop(columns=['Target Speed']), batch_first)
        self.assertTrue(pd.isna(result[0]))
        self.assertEqual(result[3], 5.0)

    def test_total_setup_is_nan_when_ramp_up_is_nan(self):
        roll2, batch_first = build_batch(target_speed=0.0)
        self.assertTrue(pd.isna(calculate_time_loss(1, roll2, batch_first)[2]))


class AwareNaiveComparisonTests(SimpleTestCase):
    """The silent-failure path worth the most attention in this file.

    to_local_ts() returns Asia/Kolkata-aware timestamps, and they are compared
    against roll2['DATE TIME']. When that column is naive -- which is what the
    project stores everywhere, since USE_TZ=False -- pandas raises:

        Invalid comparison between dtype=datetime64[ns] and Timestamp

    calculate_time_loss wraps its whole body in `except Exception`, prints the
    error and returns five NaNs. The changeover is then written with no setup
    time at all and nothing in the logs but a print. No exception surfaces, no
    alert fires, and the dashboard simply shows a row with missing numbers.

    These two tests are the contrast that makes it visible: identical inputs,
    differing only in tzinfo, produce complete results or nothing at all.
    """

    def test_aware_timestamps_produce_complete_results(self):
        roll2, batch_first = build_batch(aware=True)
        self.assertEqual(calculate_time_loss(1, roll2, batch_first)[2], 15.0)

    def test_naive_timestamps_silently_produce_all_nan(self):
        roll2, batch_first = build_batch(aware=False)
        result = calculate_time_loss(1, roll2, batch_first)
        self.assertTrue(
            all(pd.isna(v) for v in result),
            'Naive DATE TIME now yields results -- if the comparison was fixed, '
            'update this test to assert the correct values instead.',
        )
