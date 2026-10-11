"""Shift and production-date attribution.

Documentation sections 2.10 and 3.6 define the boundaries:

    Shift A: 07:00 - 15:00
    Shift B: 15:00 - 23:00
    Shift C: 23:00 - 07:00  (crosses midnight)

A changeover between 00:00 and 07:00 belongs to shift C of the PREVIOUS
production day, which is the rule most likely to be got wrong.

calculate_shift_and_date lives at module level in data_processor/tasks.py.
It was originally nested inside process_changeover_data, which made it
untestable; it is imported directly here, so re-nesting it would break this
module loudly rather than skipping these cases.
"""

import datetime

import pandas as pd
from django.test import SimpleTestCase

from data_processor.tasks import calculate_shift_and_date


def at(hour, minute=0, second=0, day=15):
    return datetime.datetime(2026, 4, day, hour, minute, second)


class ShiftBoundaryTests(SimpleTestCase):
    """Every boundary second, from both sides."""

    def test_one_second_before_shift_a_is_shift_c_previous_day(self):
        shift, production_date = calculate_shift_and_date(at(6, 59, 59))
        self.assertEqual(shift, 'C')
        self.assertEqual(production_date, datetime.date(2026, 4, 14))

    def test_shift_a_starts_at_0700(self):
        shift, production_date = calculate_shift_and_date(at(7, 0, 0))
        self.assertEqual(shift, 'A')
        self.assertEqual(production_date, datetime.date(2026, 4, 15))

    def test_one_second_before_shift_b_is_still_shift_a(self):
        self.assertEqual(calculate_shift_and_date(at(14, 59, 59))[0], 'A')

    def test_shift_b_starts_at_1500(self):
        shift, production_date = calculate_shift_and_date(at(15, 0, 0))
        self.assertEqual(shift, 'B')
        self.assertEqual(production_date, datetime.date(2026, 4, 15))

    def test_one_second_before_shift_c_is_still_shift_b(self):
        self.assertEqual(calculate_shift_and_date(at(22, 59, 59))[0], 'B')

    def test_shift_c_starts_at_2300_on_the_same_production_day(self):
        # Late-evening shift C keeps the current date; only the post-midnight
        # part of shift C rolls back.
        shift, production_date = calculate_shift_and_date(at(23, 0, 0))
        self.assertEqual(shift, 'C')
        self.assertEqual(production_date, datetime.date(2026, 4, 15))

    def test_just_before_midnight_keeps_the_current_date(self):
        self.assertEqual(calculate_shift_and_date(at(23, 59, 59))[1],
                         datetime.date(2026, 4, 15))

    def test_midnight_rolls_back_to_the_previous_production_day(self):
        shift, production_date = calculate_shift_and_date(at(0, 0, 0))
        self.assertEqual(shift, 'C')
        self.assertEqual(production_date, datetime.date(2026, 4, 14))


class ShiftCalendarRolloverTests(SimpleTestCase):
    """Date arithmetic that only breaks on a few days of the year."""

    def test_month_boundary(self):
        shift, production_date = calculate_shift_and_date(
            datetime.datetime(2026, 5, 1, 0, 30))
        self.assertEqual(shift, 'C')
        self.assertEqual(production_date, datetime.date(2026, 4, 30))

    def test_year_boundary(self):
        shift, production_date = calculate_shift_and_date(
            datetime.datetime(2026, 1, 1, 2, 0))
        self.assertEqual(shift, 'C')
        self.assertEqual(production_date, datetime.date(2025, 12, 31))

    def test_leap_day_boundary(self):
        shift, production_date = calculate_shift_and_date(
            datetime.datetime(2028, 3, 1, 3, 0))
        self.assertEqual(shift, 'C')
        self.assertEqual(production_date, datetime.date(2028, 2, 29))


class ShiftInputHandlingTests(SimpleTestCase):
    """Null, pandas and timezone-aware inputs."""

    def test_null_input_returns_none_pair(self):
        self.assertEqual(calculate_shift_and_date(pd.NaT), (None, None))

    def test_pandas_timestamp_is_accepted(self):
        self.assertEqual(
            calculate_shift_and_date(pd.Timestamp('2026-04-15 07:00:00'))[0], 'A')

    def test_aware_ist_input_keeps_its_wall_clock(self):
        # Weak on its own -- an IST-tagged 07:00 reads as 07:00 whether the
        # code strips the offset or converts to IST. Kept as a basic shape
        # check; the discriminating case is the UTC one below.
        aware = pd.Timestamp('2026-04-15 07:00:00', tz='Asia/Kolkata')
        shift, production_date = calculate_shift_and_date(aware)
        self.assertEqual(shift, 'A')
        self.assertEqual(production_date, datetime.date(2026, 4, 15))

    def test_aware_utc_input_is_stripped_not_converted(self):
        # The real regression guard for the timezone problem.
        #
        # The project stores naive wall-clock time (USE_TZ=False), so an offset
        # that arrives on a timestamp must be DISCARDED, not honoured. This
        # input distinguishes the two readings:
        #
        #   strip the offset  -> 02:00 -> shift C, production date the 14th
        #   convert to IST    -> 07:30 -> shift A, production date the 15th
        #
        # If anyone reintroduces timezone.localtime() or astimezone() here,
        # this test fails and names the regression. The IST case above would
        # not notice.
        aware = pd.Timestamp('2026-04-15 02:00:00', tz='UTC')
        shift, production_date = calculate_shift_and_date(aware)
        self.assertEqual(shift, 'C', 'offset was honoured instead of discarded')
        self.assertEqual(production_date, datetime.date(2026, 4, 14))
