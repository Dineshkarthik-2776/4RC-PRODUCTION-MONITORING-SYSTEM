"""Duty sessions: who was on the line, and when they stopped being.

A duty session is an operator's sign-in. Everything operator-wise rests on
these rows being honest about when somebody was actually there, so the
tests here are mostly about the ways a session can be left open.
"""

import datetime

from django.test import TestCase
from rest_framework.authtoken.models import Token

from data_processor.models import DutySession
from data_processor.setup_capture import (
    close_duty_sessions,
    current_duty_session,
    expired_duty_sessions,
    is_line_operator,
    open_duty_session,
)
from data_processor.tasks import end_finished_duty_sessions
from data_processor.tests.base import RealDatabaseGuard, fast_passwords
from user_authentication.models import User

D = datetime.datetime


def make_user(employee_id, role=User.Role.OPERATOR):
    return User.objects.create_user(
        employee_id=employee_id, password='line-pass-99',
        name=f'Person {employee_id}', role=role)


@fast_passwords
class DutySessionCreationTests(RealDatabaseGuard, TestCase):

    def test_an_operator_signing_in_goes_on_duty(self):
        operator = make_user('OP1')
        session = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        self.assertIsNotNone(session)
        self.assertEqual(session.shift, 'A')
        self.assertEqual(session.production_date, datetime.date(2026, 11, 8))
        self.assertTrue(session.is_open)

    def test_a_shift_incharge_does_not_go_on_duty(self):
        # Supervising is not standing at the machine; counting it would
        # credit setup minutes to somebody who did not do the setup.
        incharge = make_user('SI1', role=User.Role.SHIFT_INCHARGE)
        self.assertIsNone(open_duty_session(incharge))
        self.assertFalse(DutySession.objects.exists())

    def test_a_manager_does_not_go_on_duty(self):
        manager = make_user('MGR1', role=User.Role.MANAGER)
        self.assertIsNone(open_duty_session(manager))
        self.assertFalse(DutySession.objects.exists())

    def test_only_operators_count_as_line_operators(self):
        self.assertTrue(is_line_operator(make_user('OP2')))
        self.assertFalse(is_line_operator(
            make_user('SI2', role=User.Role.SHIFT_INCHARGE)))
        self.assertFalse(is_line_operator(
            make_user('MGR2', role=User.Role.MANAGER)))

    def test_a_night_signin_carries_the_previous_production_date(self):
        operator = make_user('OP3')
        session = open_duty_session(operator, at=D(2026, 11, 9, 2, 0))

        self.assertEqual(session.shift, 'C')
        self.assertEqual(session.production_date, datetime.date(2026, 11, 8))


@fast_passwords
class ForgottenSessionTests(RealDatabaseGuard, TestCase):

    def test_signing_in_again_closes_the_session_left_open(self):
        # The browser was shut without signing out. The old session would
        # otherwise stay open and keep collecting setup time.
        operator = make_user('OP1')
        first = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))
        open_duty_session(operator, at=D(2026, 11, 8, 10, 0))

        first.refresh_from_db()
        self.assertEqual(first.ended_at, D(2026, 11, 8, 10, 0))
        self.assertEqual(first.end_reason, DutySession.EndReason.SUPERSEDED)

    def test_only_one_session_is_ever_open_per_operator(self):
        operator = make_user('OP1')
        for hour in (8, 9, 10):
            open_duty_session(operator, at=D(2026, 11, 8, hour, 0))

        self.assertEqual(
            DutySession.objects.filter(user=operator, ended_at__isnull=True)
            .count(), 1)

    def test_another_operators_session_is_untouched(self):
        first = make_user('OP1')
        second = make_user('OP2')
        theirs = open_duty_session(first, at=D(2026, 11, 8, 9, 0))
        open_duty_session(second, at=D(2026, 11, 8, 9, 30))

        theirs.refresh_from_db()
        self.assertTrue(theirs.is_open)


@fast_passwords
class SignOutTests(RealDatabaseGuard, TestCase):

    def test_signing_out_closes_the_session(self):
        operator = make_user('OP1')
        session = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        closed = close_duty_sessions(operator, at=D(2026, 11, 8, 12, 0))

        session.refresh_from_db()
        self.assertEqual(closed, 1)
        self.assertEqual(session.ended_at, D(2026, 11, 8, 12, 0))
        self.assertEqual(session.end_reason, DutySession.EndReason.SIGNED_OUT)

    def test_signing_out_twice_closes_nothing_the_second_time(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 9, 0))
        close_duty_sessions(operator, at=D(2026, 11, 8, 12, 0))

        self.assertEqual(close_duty_sessions(operator), 0)

    def test_current_session_is_the_open_one(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 8, 0))
        latest = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        self.assertEqual(current_duty_session(operator), latest)

    def test_no_open_session_reads_as_none(self):
        operator = make_user('OP1')
        self.assertIsNone(current_duty_session(operator))


@fast_passwords
class BoundaryClampTests(RealDatabaseGuard, TestCase):
    """A session can never be recorded as running past its own shift."""

    def test_a_late_signout_is_clamped_to_the_boundary(self):
        # A browser that slept through the shift change and signed out an
        # hour late. Recording that hour would hand the operator the next
        # shift's setup time.
        operator = make_user('OP1')
        session = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        close_duty_sessions(operator, at=D(2026, 11, 8, 16, 0))

        session.refresh_from_db()
        self.assertEqual(session.ended_at, D(2026, 11, 8, 15, 0))

    def test_a_clamped_close_is_recorded_as_the_shift_ending(self):
        # Whatever triggered the close, what happened is that the shift
        # ended -- so that is what the reason says.
        operator = make_user('OP1')
        session = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        close_duty_sessions(operator, at=D(2026, 11, 8, 16, 0))

        session.refresh_from_db()
        self.assertEqual(session.end_reason, DutySession.EndReason.SHIFT_ENDED)

    def test_an_ordinary_signout_inside_the_shift_is_not_clamped(self):
        operator = make_user('OP1')
        session = open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        close_duty_sessions(operator, at=D(2026, 11, 8, 12, 30))

        session.refresh_from_db()
        self.assertEqual(session.ended_at, D(2026, 11, 8, 12, 30))
        self.assertEqual(session.end_reason, DutySession.EndReason.SIGNED_OUT)

    def test_superseding_a_stale_session_is_clamped_too(self):
        # Signing in the next morning must not stretch yesterday's
        # forgotten session across the night.
        operator = make_user('OP1')
        stale = open_duty_session(operator, at=D(2026, 11, 7, 9, 0))

        open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        stale.refresh_from_db()
        self.assertEqual(stale.ended_at, D(2026, 11, 7, 15, 0))
        self.assertEqual(stale.end_reason, DutySession.EndReason.SHIFT_ENDED)

    def test_a_clamped_session_cannot_collect_the_next_shifts_setup(self):
        from data_processor.setup_capture import rebuild_allocations
        from data_processor.models import ChangeoverSummary, SetupAllocation

        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 9, 0))
        close_duty_sessions(operator, at=D(2026, 11, 8, 20, 0))

        # A changeover during B shift, after the clamped boundary.
        changeover = ChangeoverSummary.objects.create(
            batch=1.0,
            setup_start_timestamp=D(2026, 11, 8, 17, 0),
            setup_complete_timestamp=D(2026, 11, 8, 17, 30),
            shift='B', production_date=datetime.date(2026, 11, 8))
        rebuild_allocations(changeover)

        self.assertIsNone(SetupAllocation.objects.get().operator)


@fast_passwords
class ShiftExpiryTests(RealDatabaseGuard, TestCase):

    def test_a_session_expires_at_the_next_boundary_not_eight_hours_later(self):
        # Signed in at 14:50, so ten minutes of A shift -- not a full shift.
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 14, 50))

        self.assertEqual(
            len(expired_duty_sessions(now=D(2026, 11, 8, 15, 1))), 1)

    def test_a_session_inside_its_shift_has_not_expired(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 9, 0))

        self.assertEqual(
            expired_duty_sessions(now=D(2026, 11, 8, 14, 59)), [])

    def test_a_closed_session_is_never_expired(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 9, 0))
        close_duty_sessions(operator, at=D(2026, 11, 8, 10, 0))

        self.assertEqual(
            expired_duty_sessions(now=D(2026, 11, 9, 9, 0)), [])


@fast_passwords
class ShiftSignOutTaskTests(RealDatabaseGuard, TestCase):
    """The Celery backstop that signs operators out at the boundary."""

    def _expired_session(self, employee_id='OP1'):
        operator = make_user(employee_id)
        # Yesterday morning, so it is expired whenever the suite runs.
        started = datetime.datetime.now() - datetime.timedelta(days=1)
        started = started.replace(hour=9, minute=0, second=0, microsecond=0)
        session = open_duty_session(operator, at=started)
        Token.objects.create(user=operator)
        return operator, session

    def test_the_task_closes_an_expired_session(self):
        operator, session = self._expired_session()

        result = end_finished_duty_sessions()

        session.refresh_from_db()
        self.assertEqual(result['closed'], 1)
        self.assertEqual(session.end_reason, DutySession.EndReason.SHIFT_ENDED)
        self.assertIsNotNone(session.ended_at)

    def test_it_closes_at_the_boundary_not_at_the_moment_it_runs(self):
        # The shift ended when it ended, however late the worker got to it.
        operator, session = self._expired_session()
        expected = session.started_at.replace(hour=15, minute=0)

        end_finished_duty_sessions()

        session.refresh_from_db()
        self.assertEqual(session.ended_at, expected)

    def test_it_revokes_the_token_so_the_browser_is_signed_out(self):
        operator, _ = self._expired_session()

        end_finished_duty_sessions()

        self.assertFalse(Token.objects.filter(user=operator).exists())

    def test_an_operator_inside_their_shift_keeps_their_token(self):
        operator = make_user('OP9')
        open_duty_session(operator)
        Token.objects.create(user=operator)

        end_finished_duty_sessions()

        self.assertTrue(Token.objects.filter(user=operator).exists())

    def test_a_manager_is_never_signed_out_by_the_task(self):
        # Managers get no duty session, so there is nothing for the task
        # to expire -- they stay signed in across boundaries.
        manager = make_user('MGR1', role=User.Role.MANAGER)
        Token.objects.create(user=manager)

        end_finished_duty_sessions()

        self.assertTrue(Token.objects.filter(user=manager).exists())

    def test_running_it_with_nothing_to_do_is_harmless(self):
        self.assertEqual(end_finished_duty_sessions(), {'closed': 0})
