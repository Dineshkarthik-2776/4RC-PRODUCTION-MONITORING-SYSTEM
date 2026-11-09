"""The shift calendar: A 07-15, B 15-23, C 23-07.

C is the one worth testing properly. It crosses midnight, so the hours
after midnight belong to the previous day's production date -- get that
wrong and a night shift's work lands on the wrong day's report.
"""

import datetime

from django.test import SimpleTestCase

from data_processor.shifts import (
    boundaries_between,
    shift_end_after,
    shift_for,
    shift_start_of,
)

D = datetime.datetime


class ShiftForTests(SimpleTestCase):

    def test_morning_is_a_shift(self):
        self.assertEqual(shift_for(D(2026, 11, 8, 9, 30)),
                         ('A', datetime.date(2026, 11, 8)))

    def test_afternoon_is_b_shift(self):
        self.assertEqual(shift_for(D(2026, 11, 8, 18, 0)),
                         ('B', datetime.date(2026, 11, 8)))

    def test_late_evening_is_c_shift_on_the_same_date(self):
        self.assertEqual(shift_for(D(2026, 11, 8, 23, 30)),
                         ('C', datetime.date(2026, 11, 8)))

    def test_after_midnight_belongs_to_yesterdays_production_date(self):
        # 02:00 on the 9th is C shift of the 8th: the shift began at 23:00
        # the night before.
        self.assertEqual(shift_for(D(2026, 11, 9, 2, 0)),
                         ('C', datetime.date(2026, 11, 8)))

    def test_boundaries_belong_to_the_shift_starting(self):
        self.assertEqual(shift_for(D(2026, 11, 8, 7, 0))[0], 'A')
        self.assertEqual(shift_for(D(2026, 11, 8, 15, 0))[0], 'B')
        self.assertEqual(shift_for(D(2026, 11, 8, 23, 0))[0], 'C')

    def test_one_minute_before_a_boundary_is_still_the_old_shift(self):
        self.assertEqual(shift_for(D(2026, 11, 8, 14, 59))[0], 'A')
        self.assertEqual(shift_for(D(2026, 11, 8, 22, 59))[0], 'B')
        self.assertEqual(shift_for(D(2026, 11, 8, 6, 59))[0], 'C')

    def test_agrees_with_the_pipelines_wrapper(self):
        # calculate_shift_and_date delegates here; this is the test that
        # would fail if somebody re-inlined the hours into tasks.py.
        from data_processor.tasks import calculate_shift_and_date
        for moment in (D(2026, 11, 8, 7, 0), D(2026, 11, 8, 14, 59),
                       D(2026, 11, 8, 23, 1), D(2026, 11, 9, 3, 0)):
            self.assertEqual(calculate_shift_and_date(moment), shift_for(moment))


class ShiftStartTests(SimpleTestCase):

    def test_start_of_each_shift(self):
        self.assertEqual(shift_start_of(D(2026, 11, 8, 9, 0)),
                         D(2026, 11, 8, 7, 0))
        self.assertEqual(shift_start_of(D(2026, 11, 8, 20, 0)),
                         D(2026, 11, 8, 15, 0))
        self.assertEqual(shift_start_of(D(2026, 11, 8, 23, 30)),
                         D(2026, 11, 8, 23, 0))

    def test_after_midnight_the_shift_started_yesterday(self):
        self.assertEqual(shift_start_of(D(2026, 11, 9, 3, 0)),
                         D(2026, 11, 8, 23, 0))


class ShiftEndTests(SimpleTestCase):

    def test_next_boundary_within_the_day(self):
        self.assertEqual(shift_end_after(D(2026, 11, 8, 9, 0)),
                         D(2026, 11, 8, 15, 0))
        self.assertEqual(shift_end_after(D(2026, 11, 8, 16, 0)),
                         D(2026, 11, 8, 23, 0))

    def test_after_the_last_boundary_it_rolls_to_tomorrow(self):
        self.assertEqual(shift_end_after(D(2026, 11, 8, 23, 30)),
                         D(2026, 11, 9, 7, 0))
        self.assertEqual(shift_end_after(D(2026, 11, 9, 1, 0)),
                         D(2026, 11, 9, 7, 0))

    def test_standing_exactly_on_a_boundary_gives_the_next_one(self):
        # Strictly after, so somebody signing in at 15:00 sharp gets the
        # whole of B shift rather than being signed out immediately.
        self.assertEqual(shift_end_after(D(2026, 11, 8, 15, 0)),
                         D(2026, 11, 8, 23, 0))

    def test_signing_in_late_in_a_shift_still_ends_at_the_boundary(self):
        # The point of this rule: ten minutes of A shift, not eight hours.
        self.assertEqual(shift_end_after(D(2026, 11, 8, 14, 50)),
                         D(2026, 11, 8, 15, 0))


class BoundariesBetweenTests(SimpleTestCase):

    def test_none_inside_a_short_window(self):
        self.assertEqual(
            boundaries_between(D(2026, 11, 8, 9, 0), D(2026, 11, 8, 10, 0)), [])

    def test_one_boundary(self):
        self.assertEqual(
            boundaries_between(D(2026, 11, 8, 14, 30), D(2026, 11, 8, 15, 30)),
            [D(2026, 11, 8, 15, 0)])

    def test_endpoints_are_excluded(self):
        # A window that starts exactly at a boundary is not cut there --
        # there is nothing before the cut to attribute.
        self.assertEqual(
            boundaries_between(D(2026, 11, 8, 15, 0), D(2026, 11, 8, 16, 0)), [])
        self.assertEqual(
            boundaries_between(D(2026, 11, 8, 14, 0), D(2026, 11, 8, 15, 0)), [])

    def test_across_midnight(self):
        self.assertEqual(
            boundaries_between(D(2026, 11, 8, 22, 30), D(2026, 11, 9, 8, 0)),
            [D(2026, 11, 8, 23, 0), D(2026, 11, 9, 7, 0)])

    def test_a_backwards_window_has_none(self):
        self.assertEqual(
            boundaries_between(D(2026, 11, 8, 10, 0), D(2026, 11, 8, 9, 0)), [])
