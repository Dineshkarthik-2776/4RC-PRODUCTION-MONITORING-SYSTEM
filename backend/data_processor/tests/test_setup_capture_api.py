"""The setup-capture endpoints, and rebuilding attribution from the database.

The arithmetic is tested in test_setup_allocation.py. What matters here is
that the right rows reach it, that the result is written back, and that
recording an interruption corrects the figures straight away rather than
leaving an operator looking at a number they know is wrong.
"""

import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from data_processor.models import (
    ChangeoverSummary,
    LineStoppage,
    OvershootReasons,
    SetupAllocation,
    SetupAttribution,
)
from data_processor.setup_capture import open_duty_session, rebuild_allocations
from data_processor.tasks import close_stoppages_at_shift_end
from data_processor.tests.base import RealDatabaseGuard, fast_passwords
from user_authentication.models import User

D = datetime.datetime


def make_user(employee_id, role=User.Role.OPERATOR):
    return User.objects.create_user(
        employee_id=employee_id, password='line-pass-99',
        name=f'Person {employee_id}', role=role)


def approve(manager, interruption_id):
    client = APIClient()
    client.force_authenticate(manager)
    return client.post(f'/api/line-stoppages/{interruption_id}/review/',
                       {'action': 'approve'}, format='json')


def reject(manager, interruption_id, note):
    client = APIClient()
    client.force_authenticate(manager)
    return client.post(f'/api/line-stoppages/{interruption_id}/review/',
                       {'action': 'reject', 'note': note}, format='json')


def make_changeover(batch=1.0, start=None, end=None, **kwargs):
    return ChangeoverSummary.objects.create(
        batch=batch,
        setup_start_timestamp=start or D(2026, 11, 8, 8, 0),
        setup_complete_timestamp=end or D(2026, 11, 8, 9, 0),
        shift='A',
        production_date=datetime.date(2026, 11, 8),
        **kwargs)


@fast_passwords
class RebuildAllocationTests(RealDatabaseGuard, TestCase):

    def test_a_setup_is_credited_to_the_operator_who_was_signed_in(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 7, 0))
        changeover = make_changeover()

        rebuild_allocations(changeover)

        allocation = SetupAllocation.objects.get()
        self.assertEqual(allocation.operator, operator)
        self.assertEqual(allocation.allocated_minutes, 60.0)

    def test_a_setup_with_nobody_signed_in_is_stored_unattributed(self):
        changeover = make_changeover()

        rebuild_allocations(changeover)

        allocation = SetupAllocation.objects.get()
        self.assertIsNone(allocation.operator)
        self.assertEqual(allocation.allocated_minutes, 60.0)

    def test_an_unresolved_setup_window_allocates_nothing(self):
        changeover = make_changeover()
        changeover.setup_complete_timestamp = None
        changeover.save()

        self.assertEqual(rebuild_allocations(changeover), [])
        self.assertFalse(SetupAllocation.objects.exists())

    def test_rebuilding_replaces_rather_than_duplicates(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 7, 0))
        changeover = make_changeover()

        rebuild_allocations(changeover)
        rebuild_allocations(changeover)
        rebuild_allocations(changeover)

        self.assertEqual(SetupAllocation.objects.count(), 1)

    def test_a_later_signin_corrects_the_earlier_attribution(self):
        # The pipeline runs every five minutes, so a changeover written
        # before the operator signed in gets fixed on the next pass.
        changeover = make_changeover()
        rebuild_allocations(changeover)
        self.assertIsNone(SetupAllocation.objects.get().operator)

        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 7, 0))
        rebuild_allocations(changeover)

        self.assertEqual(SetupAllocation.objects.get().operator, operator)

    def test_a_shift_change_produces_two_rows_for_one_operator(self):
        operator = make_user('OP1')
        open_duty_session(operator, at=D(2026, 11, 8, 7, 0))
        changeover = make_changeover(
            start=D(2026, 11, 8, 14, 20), end=D(2026, 11, 8, 15, 20))

        rebuild_allocations(changeover)

        rows = list(SetupAllocation.objects.order_by('segment_start'))
        self.assertEqual([(r.shift, r.allocated_minutes) for r in rows],
                         [('A', 40.0), ('B', 20.0)])
        self.assertEqual({r.operator for r in rows}, {operator})

    def test_sessions_that_never_overlap_the_window_are_ignored(self):
        night = make_user('NIGHT')
        open_duty_session(night, at=D(2026, 11, 7, 23, 0))
        from data_processor.setup_capture import close_duty_sessions
        close_duty_sessions(night, at=D(2026, 11, 8, 7, 0))

        changeover = make_changeover()
        rebuild_allocations(changeover)

        self.assertIsNone(SetupAllocation.objects.get().operator)


@fast_passwords
class InterruptionApiTests(RealDatabaseGuard, TestCase):

    def setUp(self):
        super().setUp()
        self.operator = make_user('OP1')
        open_duty_session(self.operator, at=D(2026, 11, 8, 7, 0))
        self.changeover = make_changeover()
        rebuild_allocations(self.changeover)

        self.manager = make_user('MGR1', role=User.Role.MANAGER)
        self.breakdown = OvershootReasons.objects.create(
            category='Machine', reason='Breakdown during setup')
        self.vague = OvershootReasons.objects.create(
            category='Other', reason='Other')

        self.client = APIClient()
        self.client.force_authenticate(self.operator)

    def post_pause(self, **overrides):
        payload = {
            'reason': self.breakdown.pk,
            'started_at': '2026-11-08T08:20:00',
            'ended_at': '2026-11-08T08:40:00',
        }
        payload.update(overrides)
        return self.client.post('/api/line-stoppages/', payload,
                                format='json')

    def test_an_operator_can_record_an_interruption(self):
        # Operators are read-only everywhere else. Not here: they are the
        # ones standing there when the compound runs out.
        response = self.post_pause()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(LineStoppage.objects.count(), 1)

    def test_the_recorder_is_taken_from_the_request_not_the_payload(self):
        other = make_user('OP2')
        response = self.post_pause(recorded_by=other.pk)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(LineStoppage.objects.get().recorded_by,
                         self.operator)

    def test_an_operators_pause_waits_for_approval(self):
        # It is a claim that minutes should not count against them, so
        # it changes nothing until a manager agrees.
        created = self.post_pause()

        self.assertEqual(created.data['status'], 'pending')
        self.assertFalse(created.data['counts'])
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_approval_is_what_makes_the_minutes_stop_counting(self):
        created = self.post_pause()
        approve(self.manager, created.data['id'])

        allocation = SetupAllocation.objects.get()
        self.assertEqual(allocation.interrupted_minutes, 20.0)
        self.assertEqual(allocation.allocated_minutes, 40.0)

    def test_closing_an_approved_running_pause_corrects_the_allocation(self):
        created = self.post_pause(ended_at=None)
        approve(self.manager, created.data['id'])

        response = self.client.patch(
            f"/api/line-stoppages/{created.data['id']}/",
            {'ended_at': '2026-11-08T08:30:00'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SetupAllocation.objects.get().interrupted_minutes, 10.0)

    def test_deleting_an_approved_pause_gives_the_time_back(self):
        created = self.post_pause()
        approve(self.manager, created.data['id'])
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 40.0)

        self.client.delete(f"/api/line-stoppages/{created.data['id']}/")

        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_a_manager_recording_one_needs_no_second_signature(self):
        # They are the approver; approving their own entry is theatre.
        self.client.force_authenticate(self.manager)
        created = self.post_pause()

        self.assertEqual(created.data['status'], 'approved')
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 40.0)

    def test_rejecting_gives_the_minutes_back(self):
        created = self.post_pause()
        approve(self.manager, created.data['id'])
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 40.0)

        reject(self.manager, created.data['id'], 'Line was running fine.')

        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_an_operator_cannot_approve(self):
        created = self.post_pause()
        response = self.client.post(
            f"/api/line-stoppages/{created.data['id']}/review/",
            {'action': 'approve'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_a_shift_incharge_can_approve(self):
        # Same signature as approving an overshoot reason, and the same
        # people hold it.
        created = self.post_pause()
        incharge = make_user('SI1', role=User.Role.SHIFT_INCHARGE)
        client = APIClient()
        client.force_authenticate(incharge)
        response = client.post(
            f"/api/line-stoppages/{created.data['id']}/review/",
            {'action': 'approve'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 40.0)

    def test_a_shift_incharge_recording_one_needs_no_second_signature(self):
        incharge = make_user('SI2', role=User.Role.SHIFT_INCHARGE)
        self.client.force_authenticate(incharge)
        created = self.post_pause()

        self.assertEqual(created.data['status'], 'approved')

    def test_rejecting_requires_a_reason(self):
        created = self.post_pause()
        client = APIClient()
        client.force_authenticate(self.manager)
        response = client.post(
            f"/api/line-stoppages/{created.data['id']}/review/",
            {'action': 'reject'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('note', response.data)

    def test_the_review_records_who_and_when(self):
        created = self.post_pause()
        approve(self.manager, created.data['id'])

        interruption = LineStoppage.objects.get()
        self.assertEqual(interruption.reviewed_by, self.manager)
        self.assertIsNotNone(interruption.reviewed_at)

    def test_pending_interruptions_can_be_listed_for_review(self):
        self.post_pause()
        response = self.client.get('/api/line-stoppages/?status=pending')
        self.assertEqual(len(response.data), 1)

    def test_a_stoppage_outside_any_setup_counts_for_nothing(self):
        # Allowed, unlike before: the line does stop between changeovers
        # and that is worth recording. It simply affects no allocation.
        response = self.post_pause(started_at='2026-11-08T03:00:00',
                                   ended_at='2026-11-08T04:00:00')
        approve(self.manager, response.data['id'])

        self.assertEqual(response.status_code, 201)
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_a_stoppage_overlapping_the_start_is_clipped(self):
        # Half outside. The engine clips it, so only the overlap counts.
        created = self.post_pause(started_at='2026-11-08T07:50:00',
                                  ended_at='2026-11-08T08:10:00')
        approve(self.manager, created.data['id'])

        self.assertEqual(SetupAllocation.objects.get().interrupted_minutes,
                         10.0)

    def test_a_stoppage_filling_the_whole_setup_is_accepted(self):
        response = self.post_pause(started_at='2026-11-08T08:00:00',
                                   ended_at='2026-11-08T09:00:00')
        self.assertEqual(response.status_code, 201)

    def test_a_zero_length_pause_is_refused(self):
        # Both fields default to the setup start, so submitting without
        # touching them is the easiest mistake to make here.
        response = self.post_pause(started_at='2026-11-08T08:20:00',
                                   ended_at='2026-11-08T08:20:00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('ended_at', response.data)

    def test_an_end_before_the_start_is_rejected(self):
        response = self.post_pause(ended_at='2026-11-08T08:10:00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('ended_at', response.data)

    def test_two_overlapping_pauses_are_refused(self):
        # Either a double entry or two people disagreeing. The engine
        # caps the total so the arithmetic stays sane, which hides the
        # problem instead of surfacing it.
        self.post_pause(started_at='2026-11-08T08:20:00',
                        ended_at='2026-11-08T08:40:00')
        response = self.post_pause(started_at='2026-11-08T08:30:00',
                                   ended_at='2026-11-08T08:50:00')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LineStoppage.objects.count(), 1)

    def test_pauses_that_only_touch_at_the_edge_are_allowed(self):
        # One ends exactly where the next begins: no shared minute.
        self.post_pause(started_at='2026-11-08T08:20:00',
                        ended_at='2026-11-08T08:40:00')
        response = self.post_pause(started_at='2026-11-08T08:40:00',
                                   ended_at='2026-11-08T08:50:00')
        self.assertEqual(response.status_code, 201)

    def test_a_second_pause_elsewhere_in_the_setup_is_allowed(self):
        self.post_pause(started_at='2026-11-08T08:10:00',
                        ended_at='2026-11-08T08:20:00')
        response = self.post_pause(started_at='2026-11-08T08:40:00',
                                   ended_at='2026-11-08T08:50:00')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(LineStoppage.objects.count(), 2)

    def test_editing_a_pause_does_not_collide_with_itself(self):
        created = self.post_pause(started_at='2026-11-08T08:20:00',
                                  ended_at='2026-11-08T08:40:00')
        response = self.client.patch(
            f"/api/line-stoppages/{created.data['id']}/",
            {'ended_at': '2026-11-08T08:45:00'}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_an_open_pause_blocks_the_rest_of_the_setup(self):
        # With no end it is assumed to run to the end of the setup, so
        # anything after it would overlap.
        self.post_pause(started_at='2026-11-08T08:20:00', ended_at=None)
        response = self.post_pause(started_at='2026-11-08T08:40:00',
                                   ended_at='2026-11-08T08:50:00')
        self.assertEqual(response.status_code, 400)

    def test_other_requires_an_explanation(self):
        # "Other" with no note is the same as no reason at all.
        response = self.post_pause(reason=self.vague.pk, note='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('note', response.data)

    def test_other_with_an_explanation_is_accepted(self):
        response = self.post_pause(reason=self.vague.pk, note='Power dip')
        self.assertEqual(response.status_code, 201)

    def test_a_specific_reason_needs_no_note(self):
        # "Machine / Breakdown during setup" explains itself.
        self.assertEqual(self.post_pause(note='').status_code, 201)

    def test_the_reason_comes_from_the_shared_master(self):
        # The same rows the overshoot dropdown is built from -- not a
        # second list hard-coded in the interruption model.
        created = self.post_pause()
        self.assertEqual(
            LineStoppage.objects.get().reason, self.breakdown)
        self.assertEqual(created.data['category'], 'Machine')
        self.assertEqual(created.data['reason_label'],
                         'Breakdown during setup')

    def test_an_unknown_reason_is_rejected(self):
        response = self.post_pause(reason=999999)
        self.assertEqual(response.status_code, 400)

    def test_a_reason_in_use_cannot_be_deleted_from_the_master(self):
        from django.db.models import ProtectedError
        self.post_pause()
        with self.assertRaises(ProtectedError):
            self.breakdown.delete()

    def test_the_list_can_be_filtered_to_the_open_one(self):
        self.post_pause()
        self.post_pause(started_at='2026-11-08T08:45:00', ended_at=None)

        response = self.client.get('/api/line-stoppages/?active=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertTrue(response.data[0]['is_running'])

    def test_signing_out_is_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.post_pause().status_code, 401)


@fast_passwords
class SetupTimeSummaryTests(RealDatabaseGuard, TestCase):

    def setUp(self):
        super().setUp()
        self.first = make_user('OP1')
        self.second = make_user('OP2')

        open_duty_session(self.first, at=D(2026, 11, 8, 7, 0))
        from data_processor.setup_capture import close_duty_sessions
        close_duty_sessions(self.first, at=D(2026, 11, 8, 8, 30))
        open_duty_session(self.second, at=D(2026, 11, 8, 8, 30))

        self.changeover = make_changeover()
        rebuild_allocations(self.changeover)

        self.client = APIClient()
        self.client.force_authenticate(self.first)

    def get(self, query=''):
        return self.client.get(
            f'/api/setup-time/summary/?start_date=2026-11-01'
            f'&end_date=2026-11-30{query}')

    def test_operator_wise_is_the_default_grouping(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['group_by'], 'operator')
        by_id = {row['employee_id']: row for row in response.data['rows']}
        self.assertEqual(by_id['OP1']['allocated_minutes'], 30.0)
        self.assertEqual(by_id['OP2']['allocated_minutes'], 30.0)

    def test_grouping_by_shift(self):
        response = self.get('&group_by=shift')

        self.assertEqual(response.data['rows'][0]['label'], 'A')
        self.assertEqual(response.data['rows'][0]['allocated_minutes'], 60.0)

    def test_grouping_by_date(self):
        response = self.get('&group_by=date')
        self.assertEqual(response.data['rows'][0]['label'], '2026-11-08')

    def test_an_unknown_grouping_is_rejected(self):
        response = self.get('&group_by=colour')
        self.assertEqual(response.status_code, 400)

    def test_totals_cover_every_row(self):
        response = self.get()
        self.assertEqual(response.data['totals']['allocated_minutes'], 60.0)

    def test_unattributed_time_is_reported_separately(self):
        # Visible rather than quietly dropped: a plant with hours of
        # unattributed setup has a sign-in problem worth seeing.
        from data_processor.setup_capture import close_duty_sessions
        close_duty_sessions(self.second, at=D(2026, 11, 8, 15, 0))

        make_changeover(batch=2.0, start=D(2026, 11, 9, 3, 0),
                        end=D(2026, 11, 9, 3, 30))
        rebuild_allocations(ChangeoverSummary.objects.get(batch=2.0))

        response = self.get()

        self.assertEqual(response.data['unattributed_minutes'], 30.0)
        labels = [row['label'] for row in response.data['rows']]
        self.assertIn('Unattributed', labels)

    def test_a_date_range_outside_the_data_returns_nothing(self):
        response = self.client.get(
            '/api/setup-time/summary/?start_date=2020-01-01&end_date=2020-01-31')

        self.assertEqual(response.data['rows'], [])
        self.assertEqual(response.data['totals']['allocated_minutes'], 0.0)

    def test_a_malformed_date_falls_back_instead_of_erroring(self):
        response = self.client.get('/api/setup-time/summary/?start_date=soon')
        self.assertEqual(response.status_code, 200)

    def test_it_can_be_filtered_to_one_recipe(self):
        # The toolbar's recipe filter has to reach this page too, and a
        # server-side total cannot be filtered in the browser afterwards.
        self.changeover.current_recipe = 'RECIPE B'
        self.changeover.save()

        self.assertEqual(self.get('&recipe=RECIPE B')
                         .data['totals']['allocated_minutes'], 60.0)
        self.assertEqual(self.get('&recipe=RECIPE Z')
                         .data['rows'], [])

    def test_the_recipe_filter_matches_loosely(self):
        # Same substring behaviour as the dashboard's own filter.
        self.changeover.current_recipe = 'RECIPE B'
        self.changeover.save()

        self.assertEqual(len(self.get('&recipe=ECIPE').data['rows']), 2)

    def test_it_can_be_filtered_to_one_shift(self):
        response = self.get('&group_by=shift&shift=B')
        self.assertEqual(response.data['rows'], [])

    def test_an_operator_who_never_signs_out_keeps_collecting(self):
        # The second operator's session is still open, so a setup two
        # nights later is still credited to them. That is exactly the
        # drift the shift sign-out task exists to prevent -- see
        # test_duty_sessions.ShiftSignOutTaskTests.
        make_changeover(batch=3.0, start=D(2026, 11, 9, 3, 0),
                        end=D(2026, 11, 9, 3, 30))
        rebuild_allocations(ChangeoverSummary.objects.get(batch=3.0))

        response = self.get()

        by_id = {row['employee_id']: row for row in response.data['rows']}
        self.assertEqual(by_id['OP2']['allocated_minutes'], 60.0)
        self.assertEqual(response.data['unattributed_minutes'], 0.0)

    def test_the_average_has_approved_delay_taken_off(self):
        # A breakdown is not setup work, so it must not count towards
        # the average the standard is compared against.
        created = self.client.post('/api/line-stoppages/', {
            'reason': OvershootReasons.objects.create(
                category='Machine', reason='Breakdown during setup').pk,
            'started_at': '2026-11-08T08:10:00',
            'ended_at': '2026-11-08T08:20:00',
        }, format='json')
        approve_as_manager = APIClient()
        manager = make_user('MGR9', role=User.Role.MANAGER)
        approve_as_manager.force_authenticate(manager)
        approve_as_manager.post(
            f"/api/line-stoppages/{created.data['id']}/review/",
            {'action': 'approve'}, format='json')
        rebuild_allocations(self.changeover)

        # Rows sort by setup time, so pick the operator the stoppage
        # actually fell on rather than whichever came first.
        by_id = {row['employee_id']: row for row in self.get().data['rows']}

        # Ten minutes of the stoppage fell on OP1's stint.
        self.assertEqual(by_id['OP1']['average_minutes'], 20.0)
        # The other operator's stint was untouched.
        self.assertEqual(by_id['OP2']['average_minutes'], 30.0)

    def test_the_standard_comparison_uses_the_average_without_delay(self):
        self.changeover.standard_time = 25
        self.changeover.save()

        row = self.get().data['rows'][0]

        self.assertEqual(row['average_minutes'], 30.0)
        self.assertEqual(row['variance_minutes'], 5.0)

    def test_totals_carry_the_average_too(self):
        self.assertEqual(self.get().data['totals']['average_minutes'], 60.0)

    def test_the_standard_time_is_reported_alongside_the_actual(self):
        self.changeover.standard_time = 45
        self.changeover.save()

        row = self.get().data['rows'][0]

        self.assertEqual(row['average_standard_minutes'], 45.0)
        self.assertEqual(row['changeovers_with_standard'], 1)

    def test_a_changeover_is_counted_once_however_many_segments(self):
        # This is the whole reason standard time is aggregated over
        # distinct changeovers: OP1 and OP2 each worked half of ONE
        # changeover, so neither may be charged its standard twice, and
        # the total must be 45 rather than 90.
        self.changeover.standard_time = 45
        self.changeover.save()

        response = self.get()

        for row in response.data['rows']:
            self.assertEqual(row['standard_minutes'], 45.0)
            self.assertEqual(row['changeovers_with_standard'], 1)
        self.assertEqual(response.data['totals']['standard_minutes'], 45.0)

    def test_one_operator_across_a_shift_change_counts_one_standard(self):
        # The same operator on both sides of 15:00 has two allocation
        # rows for one changeover. Summing across rows would say 90.
        from data_processor.setup_capture import close_duty_sessions
        close_duty_sessions(self.second, at=D(2026, 11, 8, 8, 30))
        open_duty_session(self.first, at=D(2026, 11, 8, 8, 30))
        rebuild_allocations(self.changeover)
        self.changeover.standard_time = 45
        self.changeover.save()

        row = self.get().data['rows'][0]

        self.assertEqual(row['setups'], 1)
        self.assertEqual(row['standard_minutes'], 45.0)

    def test_variance_says_how_far_over_the_target_they_are(self):
        self.changeover.standard_time = 20
        self.changeover.save()

        row = self.get().data['rows'][0]

        # 30 minutes allocated against a 20-minute standard.
        self.assertEqual(row['average_minutes'], 30.0)
        self.assertEqual(row['variance_minutes'], 10.0)

    def test_beating_the_standard_reads_as_a_negative_variance(self):
        self.changeover.standard_time = 50
        self.changeover.save()

        self.assertEqual(self.get().data['rows'][0]['variance_minutes'], -20.0)

    def test_a_changeover_with_no_standard_is_left_out_not_zeroed(self):
        # Counting it as zero would drag the average down and read as
        # though somebody beat a target that was never set.
        self.changeover.standard_time = None
        self.changeover.save()

        row = self.get().data['rows'][0]

        self.assertIsNone(row['average_standard_minutes'])
        self.assertIsNone(row['variance_minutes'])
        self.assertEqual(row['changeovers_with_standard'], 0)

    def test_the_average_standard_ignores_changeovers_without_one(self):
        self.changeover.standard_time = 40
        self.changeover.save()
        unmeasured = make_changeover(batch=7.0, start=D(2026, 11, 8, 10, 0),
                                     end=D(2026, 11, 8, 10, 30))
        unmeasured.standard_time = None
        unmeasured.save()
        rebuild_allocations(unmeasured)

        response = self.get()

        # Two changeovers in range, one of them with a target.
        self.assertEqual(response.data['totals']['setups'], 2)
        self.assertEqual(response.data['totals']['changeovers_with_standard'], 1)
        self.assertEqual(response.data['totals']['average_standard_minutes'],
                         40.0)

    def test_signing_in_is_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.get().status_code, 401)


@fast_passwords
class ChangeoverSetupDetailTests(RealDatabaseGuard, TestCase):

    def setUp(self):
        super().setUp()
        self.operator = make_user('OP1')
        open_duty_session(self.operator, at=D(2026, 11, 8, 7, 0))
        self.changeover = make_changeover()
        rebuild_allocations(self.changeover)

        self.client = APIClient()
        self.client.force_authenticate(self.operator)

    def test_it_returns_the_split_and_the_interruptions(self):
        reason = OvershootReasons.objects.create(
            category='Machine', reason='Breakdown during setup')
        LineStoppage.objects.create(
            reason=reason,
            started_at=D(2026, 11, 8, 8, 20), ended_at=D(2026, 11, 8, 8, 40),
            recorded_by=self.operator,
            status=LineStoppage.Status.APPROVED)
        rebuild_allocations(self.changeover)

        response = self.client.get(f'/api/setup-time/{self.changeover.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['allocations']), 1)
        self.assertEqual(len(response.data['interruptions']), 1)
        self.assertEqual(response.data['allocations'][0]['allocated_minutes'],
                         40.0)

    def test_an_unknown_changeover_is_a_404(self):
        response = self.client.get('/api/setup-time/999999/')
        self.assertEqual(response.status_code, 404)

    def test_unattributed_segments_are_labelled_not_left_null(self):
        from data_processor.setup_capture import close_duty_sessions
        close_duty_sessions(self.operator, at=D(2026, 11, 8, 8, 30))
        rebuild_allocations(self.changeover)

        response = self.client.get(f'/api/setup-time/{self.changeover.pk}/')

        names = [row['operator_name'] for row in response.data['allocations']]
        self.assertIn('Unattributed', names)


@fast_passwords
class DutyEndpointTests(RealDatabaseGuard, TestCase):

    def setUp(self):
        super().setUp()
        self.operator = make_user('OP1')
        self.client = APIClient()
        self.client.force_authenticate(self.operator)

    def test_duty_status_lists_who_is_on_the_line(self):
        open_duty_session(self.operator)

        response = self.client.get('/api/duty/status/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['on_duty']), 1)
        self.assertEqual(response.data['on_duty'][0]['employee_id'], 'OP1')

    def test_duty_status_is_empty_when_nobody_is_signed_in(self):
        response = self.client.get('/api/duty/status/')
        self.assertEqual(response.data['on_duty'], [])

    def test_duty_history_can_be_filtered_by_operator(self):
        other = make_user('OP2')
        open_duty_session(self.operator)
        open_duty_session(other)

        response = self.client.get('/api/duty/sessions/?employee_id=OP2')

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['employee_id'], 'OP2')


@fast_passwords
class SetupAttributionTests(RealDatabaseGuard, TestCase):
    """Assigning setup time nobody was signed in for.

    The thing most worth testing here is that an assignment survives the
    pipeline recomputing the changeover -- allocations are deleted and
    rebuilt every pass, so an assignment written onto one would last
    about five minutes.
    """

    def setUp(self):
        super().setUp()
        self.operator = make_user('OP1')
        self.incharge = make_user('SI1', role=User.Role.SHIFT_INCHARGE)
        self.manager = make_user('MGR1', role=User.Role.MANAGER)

        # Nobody signed in for this one.
        self.changeover = make_changeover()
        rebuild_allocations(self.changeover)

        self.client = APIClient()
        self.client.force_authenticate(self.incharge)
        self.url = f'/api/setup-time/{self.changeover.pk}/attribute/'

    def assign(self, employee_id='OP1', segment_start='2026-11-08T08:00:00',
               **extra):
        payload = {'employee_id': employee_id, 'segment_start': segment_start}
        payload.update(extra)
        return self.client.post(self.url, payload, format='json')

    def test_a_shift_incharge_can_assign_unattributed_time(self):
        self.assertIsNone(SetupAllocation.objects.get().operator)

        response = self.assign()

        self.assertEqual(response.status_code, 200)
        allocation = SetupAllocation.objects.get()
        self.assertEqual(allocation.operator, self.operator)
        self.assertTrue(allocation.is_manual)

    def test_a_manager_can_assign(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.assign().status_code, 200)

    def test_an_operator_cannot_assign(self):
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.assign().status_code, 403)

    def test_the_assignment_survives_a_rebuild(self):
        # The whole reason it is stored as an input rather than written
        # onto the allocation row.
        self.assign()

        rebuild_allocations(self.changeover)

        self.assertEqual(SetupAllocation.objects.get().operator,
                         self.operator)

    def test_it_records_who_assigned_it(self):
        self.assign(note='Covered the line while Ravi was on break.')

        attribution = SetupAttribution.objects.get()
        self.assertEqual(attribution.assigned_by, self.incharge)
        self.assertEqual(attribution.operator, self.operator)
        self.assertIn('break', attribution.note)

    def test_assigning_again_replaces_rather_than_duplicates(self):
        # A correction. The first guess must not be permanent.
        other = make_user('OP2')
        self.assign()
        self.assign(employee_id='OP2')

        self.assertEqual(SetupAttribution.objects.count(), 1)
        self.assertEqual(SetupAllocation.objects.get().operator, other)

    def test_it_can_be_taken_back(self):
        self.assign()

        response = self.client.delete(
            f'{self.url}?segment_start=2026-11-08T08:00:00')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(SetupAllocation.objects.get().operator)

    def test_a_segment_somebody_was_signed_in_for_cannot_be_reassigned(self):
        # Filling a gap, not overruling the duty record.
        make_user('OP2')
        open_duty_session(self.operator, at=D(2026, 11, 8, 7, 0))
        rebuild_allocations(self.changeover)

        response = self.assign(employee_id='OP2')

        self.assertEqual(response.status_code, 400)
        self.assertIn('segment_start', response.data)

    def test_an_unknown_segment_is_rejected(self):
        response = self.assign(segment_start='2026-11-08T12:00:00')
        self.assertEqual(response.status_code, 400)

    def test_only_an_operator_can_be_credited(self):
        # Setup time is operator-wise; a manager's name in that report
        # is a name the report is not about.
        response = self.assign(employee_id='MGR1')
        self.assertEqual(response.status_code, 400)
        self.assertIn('employee_id', response.data)

    def test_a_removed_employee_cannot_be_credited(self):
        gone = make_user('OP9')
        gone.retire()
        response = self.assign(employee_id='OP9')
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_employee_id_is_rejected(self):
        self.assertEqual(self.assign(employee_id='NOBODY').status_code, 400)

    def test_an_assigned_segment_shows_in_the_operator_report(self):
        self.assign()

        self.client.force_authenticate(self.manager)
        response = self.client.get(
            '/api/setup-time/summary/?start_date=2026-11-01'
            '&end_date=2026-11-30')

        by_id = {row['employee_id']: row for row in response.data['rows']}
        self.assertIn('OP1', by_id)
        self.assertEqual(response.data['unattributed_minutes'], 0.0)


@fast_passwords
class AssignableOperatorsTests(RealDatabaseGuard, TestCase):

    def setUp(self):
        super().setUp()
        self.incharge = make_user('SI1', role=User.Role.SHIFT_INCHARGE)
        self.client = APIClient()
        self.client.force_authenticate(self.incharge)

    def test_a_shift_incharge_can_read_the_operator_list(self):
        # Without being able to administer accounts, which is the
        # managers-only endpoint.
        make_user('OP1')
        response = self.client.get('/api/duty/operators/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['employee_id'] for row in response.data], ['OP1'])

    def test_it_lists_only_operators(self):
        make_user('OP1')
        make_user('MGR1', role=User.Role.MANAGER)

        response = self.client.get('/api/duty/operators/')

        self.assertEqual([row['employee_id'] for row in response.data], ['OP1'])

    def test_removed_employees_are_left_out(self):
        make_user('OP1')
        gone = make_user('OP9')
        gone.retire()

        response = self.client.get('/api/duty/operators/')

        self.assertEqual([row['employee_id'] for row in response.data], ['OP1'])

    def test_an_operator_cannot_read_it(self):
        self.client.force_authenticate(make_user('OP1'))
        self.assertEqual(
            self.client.get('/api/duty/operators/').status_code, 403)


@fast_passwords
class LiveStoppageTests(RealDatabaseGuard, TestCase):
    """Recording a stoppage while it is happening.

    The case the whole decoupling exists for: the line stops, the
    operator records it there and then, and the changeover it belongs to
    has not been detected yet -- it may not even have finished. The
    minutes still have to land on the right operator afterwards.
    """

    def setUp(self):
        super().setUp()
        self.operator = make_user('OP1')
        self.manager = make_user('MGR1', role=User.Role.MANAGER)
        self.reason = OvershootReasons.objects.create(
            category='Material', reason='Compound not ready')

        self.client = APIClient()
        self.client.force_authenticate(self.operator)

    def report_stopped(self, at='2026-11-08T08:20:00'):
        return self.client.post('/api/line-stoppages/', {
            'reason': self.reason.pk,
            'started_at': at,
            'ended_at': None,
        }, format='json')

    def report_running(self, stoppage_id, at='2026-11-08T08:40:00'):
        return self.client.patch(f'/api/line-stoppages/{stoppage_id}/',
                                 {'ended_at': at}, format='json')

    def test_a_stoppage_can_be_recorded_with_no_changeover_in_existence(self):
        # Nothing has been detected yet. This used to be impossible.
        self.assertFalse(ChangeoverSummary.objects.exists())

        response = self.report_stopped()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['is_running'])

    def test_the_line_reports_itself_as_stopped(self):
        self.report_stopped()

        response = self.client.get('/api/line-stoppages/current/')

        self.assertTrue(response.data['stopped'])
        self.assertEqual(response.data['stoppage']['reason_label'],
                         'Compound not ready')

    def test_closing_it_reports_the_line_running_again(self):
        created = self.report_stopped()
        self.report_running(created.data['id'])

        response = self.client.get('/api/line-stoppages/current/')

        self.assertFalse(response.data['stopped'])
        self.assertIsNone(response.data['stoppage'])

    def test_the_line_cannot_be_stopped_twice_at_once(self):
        self.report_stopped()
        response = self.report_stopped(at='2026-11-08T08:30:00')

        self.assertEqual(response.status_code, 400)
        self.assertIn('started_at', response.data)

    def test_it_attaches_to_a_changeover_detected_afterwards(self):
        # The heart of it. The stoppage is recorded live, the changeover
        # appears later, and the minutes land correctly anyway.
        created = self.report_stopped()
        self.report_running(created.data['id'])
        approve(self.manager, created.data['id'])

        open_duty_session(self.operator, at=D(2026, 11, 8, 7, 0))
        changeover = make_changeover()
        rebuild_allocations(changeover)

        allocation = SetupAllocation.objects.get()
        self.assertEqual(allocation.operator, self.operator)
        self.assertEqual(allocation.interrupted_minutes, 20.0)
        self.assertEqual(allocation.allocated_minutes, 40.0)

    def test_an_unapproved_live_stoppage_still_changes_nothing(self):
        created = self.report_stopped()
        self.report_running(created.data['id'])

        changeover = make_changeover()
        rebuild_allocations(changeover)

        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_a_stoppage_spanning_two_changeovers_is_clipped_to_each(self):
        created = self.report_stopped(at='2026-11-08T08:50:00')
        self.report_running(created.data['id'], at='2026-11-08T10:10:00')
        approve(self.manager, created.data['id'])

        first = make_changeover(batch=1.0)                       # 08:00-09:00
        second = make_changeover(batch=2.0, start=D(2026, 11, 8, 10, 0),
                                 end=D(2026, 11, 8, 11, 0))
        rebuild_allocations(first)
        rebuild_allocations(second)

        by_batch = {
            row.changeover.batch: row.interrupted_minutes
            for row in SetupAllocation.objects.select_related('changeover')
        }
        self.assertEqual(by_batch[1.0], 10.0)   # 08:50-09:00
        self.assertEqual(by_batch[2.0], 10.0)   # 10:00-10:10

    def test_a_stoppage_between_changeovers_affects_neither(self):
        # Previously unrepresentable: there was no changeover to hang it
        # on, so the stoppage simply could not be recorded.
        created = self.report_stopped(at='2026-11-08T09:10:00')
        self.report_running(created.data['id'], at='2026-11-08T09:50:00')
        approve(self.manager, created.data['id'])

        changeover = make_changeover()
        rebuild_allocations(changeover)

        self.assertEqual(LineStoppage.objects.count(), 1)
        self.assertEqual(SetupAllocation.objects.get().interrupted_minutes, 0.0)

    def test_approving_after_the_changeover_exists_corrects_it(self):
        created = self.report_stopped()
        self.report_running(created.data['id'])
        changeover = make_changeover()
        rebuild_allocations(changeover)
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

        approve(self.manager, created.data['id'])

        # Found by overlap and recomputed, with no foreign key to follow.
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 40.0)

    def test_deleting_a_stoppage_gives_the_minutes_back(self):
        created = self.report_stopped()
        self.report_running(created.data['id'])
        approve(self.manager, created.data['id'])
        changeover = make_changeover()
        rebuild_allocations(changeover)
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 40.0)

        self.client.delete(f"/api/line-stoppages/{created.data['id']}/")

        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_an_operator_can_record_but_not_approve(self):
        created = self.report_stopped()
        response = self.client.post(
            f"/api/line-stoppages/{created.data['id']}/review/",
            {'action': 'approve'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_a_rejected_stoppage_does_not_block_a_new_one(self):
        created = self.report_stopped()
        reject(self.manager, created.data['id'], 'Line was running.')

        response = self.report_stopped(at='2026-11-08T08:25:00')

        self.assertEqual(response.status_code, 201)


@fast_passwords
class ShiftBoundaryHandoverTests(RealDatabaseGuard, TestCase):
    """An open stoppage when the shift ends.

    Left running it would keep matching every later setup window and,
    once approved, remove the setup time from all of them -- damage with
    no upper bound from one forgotten button. So it is closed at the
    boundary and the incoming shift is asked whether the line is still
    down.
    """

    def setUp(self):
        super().setUp()
        self.operator = make_user('OP1')
        self.manager = make_user('MGR1', role=User.Role.MANAGER)
        self.reason = OvershootReasons.objects.create(
            category='Machine', reason='Breakdown during setup')

        self.client = APIClient()
        self.client.force_authenticate(self.operator)

    def open_stoppage(self, started_at):
        return LineStoppage.objects.create(
            reason=self.reason, started_at=started_at,
            recorded_by=self.operator,
            status=LineStoppage.Status.APPROVED)

    def yesterday_morning(self):
        started = datetime.datetime.now() - datetime.timedelta(days=1)
        return started.replace(hour=9, minute=0, second=0, microsecond=0)

    def test_the_task_closes_it_at_the_boundary(self):
        started = self.yesterday_morning()
        stoppage = self.open_stoppage(started)

        close_stoppages_at_shift_end()

        stoppage.refresh_from_db()
        self.assertEqual(stoppage.ended_at, started.replace(hour=15, minute=0))
        self.assertEqual(stoppage.boundary_handover, 'pending')

    def test_a_stoppage_inside_its_shift_is_left_alone(self):
        stoppage = self.open_stoppage(datetime.datetime.now())

        self.assertEqual(close_stoppages_at_shift_end(), {'closed': 0})

        stoppage.refresh_from_db()
        self.assertIsNone(stoppage.ended_at)

    def test_it_cannot_eat_a_later_changeover(self):
        # The whole reason for closing it. Without the boundary close,
        # this changeover's entire setup would read as delay.
        self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()

        changeover = make_changeover()
        rebuild_allocations(changeover)

        self.assertEqual(SetupAllocation.objects.get().interrupted_minutes, 0.0)
        self.assertEqual(SetupAllocation.objects.get().allocated_minutes, 60.0)

    def test_the_next_shift_is_asked_about_it(self):
        self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()

        response = self.client.get('/api/line-stoppages/current/')

        self.assertFalse(response.data['stopped'])
        self.assertIsNotNone(response.data['handover'])
        self.assertEqual(response.data['handover']['reason_label'],
                         'Breakdown during setup')

    def test_still_down_carries_it_on_from_the_boundary(self):
        # No minutes lost: the new stoppage starts where the old one was
        # cut, not when somebody got round to answering.
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()
        stoppage.refresh_from_db()
        boundary = stoppage.ended_at

        response = self.client.post(
            f'/api/line-stoppages/{stoppage.pk}/handover/',
            {'answer': 'continued'}, format='json')

        self.assertEqual(response.status_code, 201)
        carried = LineStoppage.objects.exclude(pk=stoppage.pk).get()
        self.assertEqual(carried.started_at, boundary)
        self.assertIsNone(carried.ended_at)
        self.assertEqual(carried.reason, self.reason)

    def test_already_resumed_closes_the_question(self):
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()

        response = self.client.post(
            f'/api/line-stoppages/{stoppage.pk}/handover/',
            {'answer': 'ended'}, format='json')

        self.assertEqual(response.status_code, 200)
        stoppage.refresh_from_db()
        self.assertEqual(stoppage.boundary_handover, 'ended')
        self.assertEqual(LineStoppage.objects.count(), 1)

    def test_the_question_is_not_asked_twice(self):
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()
        self.client.post(f'/api/line-stoppages/{stoppage.pk}/handover/',
                         {'answer': 'ended'}, format='json')

        self.assertIsNone(
            self.client.get('/api/line-stoppages/current/').data['handover'])

    def test_answering_twice_is_refused(self):
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()
        self.client.post(f'/api/line-stoppages/{stoppage.pk}/handover/',
                         {'answer': 'ended'}, format='json')

        response = self.client.post(
            f'/api/line-stoppages/{stoppage.pk}/handover/',
            {'answer': 'continued'}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_a_carried_stoppage_is_closed_at_its_own_boundary_too(self):
        # It must not become an immortal stoppage by being carried.
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()
        self.client.post(f'/api/line-stoppages/{stoppage.pk}/handover/',
                         {'answer': 'continued'}, format='json')

        close_stoppages_at_shift_end()

        carried = LineStoppage.objects.exclude(pk=stoppage.pk).get()
        self.assertIsNotNone(carried.ended_at)
        self.assertEqual(carried.boundary_handover, 'pending')

    def test_anyone_who_can_record_may_answer(self):
        # The person who opened it may have gone home mid-breakdown.
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()

        other = make_user('OP2')
        client = APIClient()
        client.force_authenticate(other)
        response = client.post(f'/api/line-stoppages/{stoppage.pk}/handover/',
                               {'answer': 'ended'}, format='json')

        self.assertEqual(response.status_code, 200)

    def test_a_nonsense_answer_is_refused(self):
        stoppage = self.open_stoppage(self.yesterday_morning())
        close_stoppages_at_shift_end()

        response = self.client.post(
            f'/api/line-stoppages/{stoppage.pk}/handover/',
            {'answer': 'maybe'}, format='json')

        self.assertEqual(response.status_code, 400)
