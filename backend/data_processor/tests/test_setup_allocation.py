"""Splitting a setup window across operators and shifts.

The five scenarios in the process flow diagram are the specification, and
three of them come with worked numbers -- A 40 / B 20, Op1 25 / Op2 15,
Op1 30 / Op2 30. Those appear below as assertions, so the arithmetic is
checked against the document rather than against itself.

Pure functions, no database: this is where the rules live, so this is
where they should break first if somebody changes them.
"""

import datetime

from django.test import SimpleTestCase

from data_processor.setup_allocation import (
    DutySpan,
    Pause,
    allocate_setup_time,
)

D = datetime.datetime


def on_duty(key, start, end=None):
    return DutySpan(operator_key=key, start=start, end=end)


class ScenarioOneTests(SimpleTestCase):
    """Started and completed in the same shift by the same operator."""

    def test_the_whole_setup_goes_to_one_operator(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 15, 0))])

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].operator_key, 'OP1')
        self.assertEqual(segments[0].shift, 'A')
        self.assertEqual(segments[0].allocated_minutes, 60.0)


class ScenarioTwoTests(SimpleTestCase):
    """Setup crosses a shift change; the same operator works both sides."""

    def test_split_at_the_boundary_as_forty_and_twenty(self):
        # The diagram's own example: A Shift 40 min, B Shift 20 min.
        segments = allocate_setup_time(
            D(2026, 11, 8, 14, 20), D(2026, 11, 8, 15, 20),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))])

        self.assertEqual(len(segments), 2)
        self.assertEqual(
            [(s.shift, s.allocated_minutes) for s in segments],
            [('A', 40.0), ('B', 20.0)])

    def test_both_pieces_credit_the_same_operator(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 14, 20), D(2026, 11, 8, 15, 20),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))])

        self.assertEqual({s.operator_key for s in segments}, {'OP1'})

    def test_the_c_shift_piece_carries_yesterdays_production_date(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 22, 50), D(2026, 11, 9, 0, 10),
            [on_duty('OP1', D(2026, 11, 8, 15, 0))])

        by_shift = {s.shift: s for s in segments}
        self.assertEqual(by_shift['B'].production_date, datetime.date(2026, 11, 8))
        self.assertEqual(by_shift['C'].production_date, datetime.date(2026, 11, 8))


class ScenarioThreeTests(SimpleTestCase):
    """Operator changes partway through, inside one shift."""

    def test_split_at_the_handover_as_twentyfive_and_fifteen(self):
        # The diagram's own example: Op 1 - 25 min, Op 2 - 15 min.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 8, 40),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 8, 25)),
             on_duty('OP2', D(2026, 11, 8, 8, 25))])

        self.assertEqual(
            [(s.operator_key, s.allocated_minutes) for s in segments],
            [('OP1', 25.0), ('OP2', 15.0)])

    def test_both_pieces_stay_in_the_same_shift(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 8, 40),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 8, 25)),
             on_duty('OP2', D(2026, 11, 8, 8, 25))])

        self.assertEqual({s.shift for s in segments}, {'A'})

    def test_the_handover_minute_goes_to_the_arriving_operator(self):
        # Attribution is decided at each piece's midpoint, so the instant
        # of handover is not credited to the person leaving.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 8, 40),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 8, 25)),
             on_duty('OP2', D(2026, 11, 8, 8, 25))])

        arriving = segments[1]
        self.assertEqual(arriving.operator_key, 'OP2')
        self.assertEqual(arriving.start, D(2026, 11, 8, 8, 25))


class ScenarioFourTests(SimpleTestCase):
    """Shift change and operator change at the same moment."""

    def test_split_thirty_and_thirty_across_two_shifts(self):
        # The diagram's own example: Op 1 - 30 min, Op 2 - 30 min.
        segments = allocate_setup_time(
            D(2026, 11, 8, 14, 30), D(2026, 11, 8, 15, 30),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 15, 0)),
             on_duty('OP2', D(2026, 11, 8, 15, 0))])

        self.assertEqual(
            [(s.operator_key, s.shift, s.allocated_minutes) for s in segments],
            [('OP1', 'A', 30.0), ('OP2', 'B', 30.0)])

    def test_a_late_handover_puts_both_operators_in_the_new_shift(self):
        # The outgoing operator stayed ten minutes past the boundary, so
        # they own the first ten minutes of B shift as well as the end of A.
        segments = allocate_setup_time(
            D(2026, 11, 8, 14, 30), D(2026, 11, 8, 15, 30),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 15, 10)),
             on_duty('OP2', D(2026, 11, 8, 15, 10))])

        self.assertEqual(
            [(s.operator_key, s.shift, s.allocated_minutes) for s in segments],
            [('OP1', 'A', 30.0), ('OP1', 'B', 10.0), ('OP2', 'B', 20.0)])


class ScenarioFiveTests(SimpleTestCase):
    """Interrupted setup: the paused minutes are not setup time."""

    def test_a_pause_is_subtracted_from_the_allocation(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))],
            [Pause(D(2026, 11, 8, 8, 20), D(2026, 11, 8, 8, 40))])

        segment = segments[0]
        self.assertEqual(segment.gross_minutes, 60.0)
        self.assertEqual(segment.interrupted_minutes, 20.0)
        self.assertEqual(segment.allocated_minutes, 40.0)

    def test_a_pause_spanning_a_shift_change_hits_both_sides(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 14, 30), D(2026, 11, 8, 15, 30),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))],
            [Pause(D(2026, 11, 8, 14, 50), D(2026, 11, 8, 15, 20))])

        self.assertEqual(
            [(s.shift, s.interrupted_minutes, s.allocated_minutes)
             for s in segments],
            [('A', 10.0, 20.0), ('B', 20.0, 10.0)])

    def test_a_pause_reaching_outside_the_window_is_clipped(self):
        # A breakdown that began before the setup did only counts from the
        # moment the setup started.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))],
            [Pause(D(2026, 11, 8, 7, 30), D(2026, 11, 8, 8, 15))])

        self.assertEqual(segments[0].interrupted_minutes, 15.0)

    def test_a_pause_still_running_counts_to_the_end_of_the_setup(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))],
            [Pause(D(2026, 11, 8, 8, 45), None)])

        self.assertEqual(segments[0].interrupted_minutes, 15.0)

    def test_overlapping_pauses_cannot_subtract_more_than_the_segment(self):
        # Two people logging the same breakdown would otherwise drive the
        # allocation negative.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))],
            [Pause(D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0)),
             Pause(D(2026, 11, 8, 8, 10), D(2026, 11, 8, 8, 50))])

        self.assertEqual(segments[0].allocated_minutes, 0.0)
        self.assertGreaterEqual(segments[0].interrupted_minutes, 0.0)

    def test_a_fully_interrupted_setup_allocates_nothing(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))],
            [Pause(D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0))])

        self.assertEqual(sum(s.allocated_minutes for s in segments), 0.0)


class UnattributedTimeTests(SimpleTestCase):
    """Nobody signed in is an answer, not a failure."""

    def test_setup_with_nobody_on_the_line_is_unattributed(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 3, 0), D(2026, 11, 8, 3, 30), [])

        self.assertEqual(len(segments), 1)
        self.assertIsNone(segments[0].operator_key)
        self.assertEqual(segments[0].allocated_minutes, 30.0)

    def test_time_before_an_operator_signs_in_is_unattributed(self):
        # The setup was already running when they arrived; crediting them
        # with the earlier minutes would be inventing attendance.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 8, 20))])

        self.assertEqual(
            [(s.operator_key, s.allocated_minutes) for s in segments],
            [(None, 20.0), ('OP1', 40.0)])

    def test_time_after_an_operator_signs_out_is_unattributed(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 8, 30))])

        self.assertEqual(
            [(s.operator_key, s.allocated_minutes) for s in segments],
            [('OP1', 30.0), (None, 30.0)])


class ConservationTests(SimpleTestCase):
    """Whatever the split, the minutes have to add up."""

    def test_allocated_plus_interrupted_equals_the_window(self):
        start, end = D(2026, 11, 8, 14, 10), D(2026, 11, 8, 15, 50)
        segments = allocate_setup_time(
            start, end,
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 15, 0)),
             on_duty('OP2', D(2026, 11, 8, 15, 0))],
            [Pause(D(2026, 11, 8, 14, 40), D(2026, 11, 8, 15, 10))])

        window = (end - start).total_seconds() / 60.0
        total = sum(s.allocated_minutes + s.interrupted_minutes
                    for s in segments)
        self.assertAlmostEqual(total, window, places=6)

    def test_segments_tile_the_window_without_gaps_or_overlap(self):
        start, end = D(2026, 11, 8, 22, 30), D(2026, 11, 9, 8, 15)
        segments = allocate_setup_time(
            start, end,
            [on_duty('OP1', D(2026, 11, 8, 15, 0), D(2026, 11, 8, 23, 0)),
             on_duty('OP2', D(2026, 11, 8, 23, 0), D(2026, 11, 9, 7, 0)),
             on_duty('OP3', D(2026, 11, 9, 7, 0))])

        self.assertEqual(segments[0].start, start)
        self.assertEqual(segments[-1].end, end)
        for earlier, later in zip(segments, segments[1:]):
            self.assertEqual(earlier.end, later.start)


class DegenerateInputTests(SimpleTestCase):

    def test_a_missing_start_or_end_allocates_nothing(self):
        # An in-flight changeover the signal has not resolved yet.
        self.assertEqual(allocate_setup_time(None, D(2026, 11, 8, 9, 0)), [])
        self.assertEqual(allocate_setup_time(D(2026, 11, 8, 8, 0), None), [])

    def test_a_zero_length_window_allocates_nothing(self):
        moment = D(2026, 11, 8, 8, 0)
        self.assertEqual(allocate_setup_time(moment, moment), [])

    def test_an_end_before_the_start_allocates_nothing(self):
        self.assertEqual(
            allocate_setup_time(D(2026, 11, 8, 9, 0), D(2026, 11, 8, 8, 0)), [])

    def test_duty_spans_outside_the_window_are_ignored(self):
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 15, 0)),
             on_duty('NIGHT', D(2026, 11, 7, 23, 0), D(2026, 11, 8, 7, 0))])

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].operator_key, 'OP1')


class MergeTests(SimpleTestCase):

    def test_a_signout_and_straight_back_in_is_one_stretch(self):
        # Two cut points, but nothing actually changed across them, so
        # reporting two segments would overstate the fragmentation.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('OP1', D(2026, 11, 8, 7, 0), D(2026, 11, 8, 8, 30)),
             on_duty('OP1', D(2026, 11, 8, 8, 30))])

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].allocated_minutes, 60.0)

    def test_the_same_operator_either_side_of_a_boundary_is_not_merged(self):
        # Same person, but the shifts have to be reported separately.
        segments = allocate_setup_time(
            D(2026, 11, 8, 14, 30), D(2026, 11, 8, 15, 30),
            [on_duty('OP1', D(2026, 11, 8, 7, 0))])

        self.assertEqual(len(segments), 2)


class OverlappingSessionTests(SimpleTestCase):

    def test_the_later_signin_wins(self):
        # A forgotten sign-out on a second terminal. The newer session is
        # the better guess at who is actually standing there.
        segments = allocate_setup_time(
            D(2026, 11, 8, 8, 0), D(2026, 11, 8, 9, 0),
            [on_duty('STALE', D(2026, 11, 8, 6, 0)),
             on_duty('OP2', D(2026, 11, 8, 7, 30))])

        self.assertEqual({s.operator_key for s in segments}, {'OP2'})
