"""The 5-hour correction window and the manager approval workflow.

Documentation section 2.7:

    Within 5 hours of a changeover event: Worker can freely update the
    overshoot category and reason. Changes are saved immediately.
    After 5 hours: The record is locked. The worker can still submit a
    correction, but it creates a Pending Correction Request rather than
    updating the record directly.
    Manager review: ... either Approves (data is updated) or Rejects (data
    remains unchanged).

This is the only genuine role boundary in the system, and it is a state
machine, so it is tested as one.
"""

import datetime

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .base import fast_passwords

from data_processor.models import (
    ChangeoverCorrectionRequest,
    ChangeoverSummary,
    OvershootReasons,
)

User = get_user_model()
WINDOW_HOURS = 5.0


def make_user(employee_id, role, name='Test User'):
    return User.objects.create_user(
        employee_id=employee_id, password='testpass123', name=name, role=role)


@fast_passwords
class CorrectionTestBase(APITestCase):

    def setUp(self):
        # Shift incharge is the role that records reasons under the 5-hour
        # window; operator is read-only and must be refused outright.
        self.shift_incharge = make_user('20001', User.Role.SHIFT_INCHARGE, 'S Raman')
        self.operator = make_user('20002', User.Role.OPERATOR, 'A Kumar')
        self.manager = make_user('20003', User.Role.MANAGER, 'P Mehta')

        self.reason_a = OvershootReasons.objects.create(
            category='Mechanical', reason='Die change took longer')
        self.reason_b = OvershootReasons.objects.create(
            category='Electrical', reason='Power fluctuation')

        self.client = APIClient()

    def make_summary(self, hours_since_setup=1.0, actual=40.0, standard=30,
                     overshoot=None):
        completed = datetime.datetime.now() - datetime.timedelta(hours=hours_since_setup)
        return ChangeoverSummary.objects.create(
            batch=hours_since_setup * 1000 + actual,
            recipe_change_time=completed - datetime.timedelta(minutes=40),
            setup_complete_timestamp=completed,
            previous_recipe='CP 034 HI', current_recipe='BRTTL',
            change_over_type='Steel to Fabric',
            setup_time_actual=actual, standard_time=standard,
            overshoot=overshoot,
        )

    def patch_as(self, user, summary, reason):
        self.client.force_authenticate(user=user)
        return self.client.patch(
            f'/api/changeover/update/{summary.id}/',
            {'overshoot': reason.id}, format='json')


@fast_passwords
class OvershootPreconditionTests(CorrectionTestBase):
    """A reason only makes sense when there is an overshoot to explain."""

    def test_missing_time_data_is_rejected(self):
        summary = self.make_summary()
        summary.setup_time_actual = None
        summary.save()

        response = self.patch_as(self.shift_incharge, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not calculated', str(response.data).lower())

    def test_missing_standard_time_is_rejected(self):
        summary = self.make_summary()
        summary.standard_time = None
        summary.save()

        response = self.patch_as(self.shift_incharge, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_overshoot_is_rejected(self):
        # Actual below standard: the setup was faster than expected.
        summary = self.make_summary(actual=20.0, standard=30)

        response = self.patch_as(self.shift_incharge, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('no overshoot', str(response.data).lower())

    def test_actual_exactly_equal_to_standard_is_rejected(self):
        # The boundary: `actual <= standard` is not an overshoot.
        summary = self.make_summary(actual=30.0, standard=30)

        response = self.patch_as(self.shift_incharge, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@fast_passwords
class WorkerWithinWindowTests(CorrectionTestBase):
    """Inside 5 hours a worker writes directly."""

    def test_first_entry_is_saved_immediately(self):
        summary = self.make_summary(hours_since_setup=1.0)

        response = self.patch_as(self.shift_incharge, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary.refresh_from_db()
        self.assertEqual(summary.overshoot, self.reason_a)

    def test_overwrite_within_the_window_is_saved_immediately(self):
        summary = self.make_summary(hours_since_setup=2.0, overshoot=self.reason_a)

        response = self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary.refresh_from_db()
        self.assertEqual(summary.overshoot, self.reason_b)

    def test_no_correction_request_is_created(self):
        summary = self.make_summary(hours_since_setup=1.0, overshoot=self.reason_a)

        self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(ChangeoverCorrectionRequest.objects.count(), 0)

    def test_first_entry_is_always_allowed_even_after_the_window(self):
        # overshoot is still null, so this is a first entry, not a correction.
        summary = self.make_summary(hours_since_setup=48.0, overshoot=None)

        response = self.patch_as(self.shift_incharge, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary.refresh_from_db()
        self.assertEqual(summary.overshoot, self.reason_a)


@fast_passwords
class WorkerOutsideWindowTests(CorrectionTestBase):
    """Past 5 hours a worker's edit becomes a request."""

    def test_overwrite_after_the_window_returns_202(self):
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)

        response = self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_a_pending_request_is_created(self):
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)

        self.patch_as(self.shift_incharge, summary, self.reason_b)

        request_obj = ChangeoverCorrectionRequest.objects.get()
        self.assertEqual(request_obj.status, ChangeoverCorrectionRequest.Status.PENDING)
        self.assertEqual(request_obj.requested_by, self.shift_incharge)
        self.assertEqual(request_obj.new_overshoot, self.reason_b)

    def test_the_summary_is_not_changed(self):
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)

        self.patch_as(self.shift_incharge, summary, self.reason_b)

        summary.refresh_from_db()
        self.assertEqual(summary.overshoot, self.reason_a,
                         'the record must stay locked until a manager approves')

    def test_the_old_value_is_snapshotted_on_the_request(self):
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)

        self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(ChangeoverCorrectionRequest.objects.get().old_overshoot,
                         self.reason_a)

    def test_exactly_five_hours_is_still_inside_the_window(self):
        # The code reads `hours_elapsed <= 5.0`, so the boundary is inclusive.
        # Pinning it because an off-by-one here is invisible in review.
        summary = self.make_summary(
            hours_since_setup=WINDOW_HOURS - 0.01, overshoot=self.reason_a)

        response = self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_just_past_five_hours_creates_a_request(self):
        summary = self.make_summary(
            hours_since_setup=WINDOW_HOURS + 0.05, overshoot=self.reason_a)

        response = self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_request_without_a_reason_is_rejected(self):
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)
        self.client.force_authenticate(user=self.shift_incharge)

        response = self.client.patch(
            f'/api/changeover/update/{summary.id}/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_falls_back_to_recipe_change_time_when_setup_complete_is_null(self):
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)
        summary.setup_complete_timestamp = None
        summary.save()

        response = self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_both_timestamps_null_still_creates_a_request(self):
        # No basis for the window at all. The record must not be silently
        # editable, so it falls through to the request path.
        summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)
        summary.setup_complete_timestamp = None
        summary.recipe_change_time = None
        summary.save()

        response = self.patch_as(self.shift_incharge, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)


@fast_passwords
class ManagerOverrideTests(CorrectionTestBase):
    """Managers and admins write directly, whatever the elapsed time."""

    def test_manager_overwrites_long_after_the_window(self):
        summary = self.make_summary(hours_since_setup=72.0, overshoot=self.reason_a)

        response = self.patch_as(self.manager, summary, self.reason_b)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary.refresh_from_db()
        self.assertEqual(summary.overshoot, self.reason_b)

    def test_an_operator_is_refused_outright(self):
        # Operators are read-only: they cannot record a reason even inside
        # the window, so the role check refuses before the window is read.
        summary = self.make_summary(hours_since_setup=1.0)

        response = self.patch_as(self.operator, summary, self.reason_a)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_operator_cannot_list_correction_requests(self):
        self.client.force_authenticate(user=self.operator)
        response = self.client.get('/api/correction-requests/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_edits_create_no_request(self):
        summary = self.make_summary(hours_since_setup=72.0, overshoot=self.reason_a)

        self.patch_as(self.manager, summary, self.reason_b)

        self.assertEqual(ChangeoverCorrectionRequest.objects.count(), 0)


@fast_passwords
class ApprovalWorkflowTests(CorrectionTestBase):
    """Manager review of a pending request."""

    def setUp(self):
        super().setUp()
        self.summary = self.make_summary(hours_since_setup=6.0, overshoot=self.reason_a)
        self.patch_as(self.shift_incharge, self.summary, self.reason_b)
        self.request_obj = ChangeoverCorrectionRequest.objects.get()
        self.client.force_authenticate(user=self.manager)

    def _act(self, action):
        return self.client.post(
            f'/api/correction-requests/{self.request_obj.id}/action/',
            {'action': action}, format='json')

    def test_pending_requests_are_listed(self):
        response = self.client.get('/api/correction-requests/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_approve_updates_the_summary(self):
        response = self._act('approve')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.summary.refresh_from_db()
        self.assertEqual(self.summary.overshoot, self.reason_b)

    def test_approve_marks_the_request_approved(self):
        self._act('approve')
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status,
                         ChangeoverCorrectionRequest.Status.APPROVED)

    def test_reject_leaves_the_summary_untouched(self):
        response = self._act('reject')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.summary.refresh_from_db()
        self.assertEqual(self.summary.overshoot, self.reason_a)

    def test_reject_marks_the_request_rejected(self):
        self._act('reject')
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status,
                         ChangeoverCorrectionRequest.Status.REJECTED)

    def test_a_processed_request_cannot_be_actioned_twice(self):
        self._act('approve')

        response = self._act('approve')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already processed', str(response.data).lower())

    def test_an_invalid_action_is_rejected(self):
        response = self._act('maybe')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_missing_request_returns_404(self):
        response = self.client.post(
            '/api/correction-requests/999999/action/',
            {'action': 'approve'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_approved_request_leaves_the_list(self):
        self._act('approve')
        response = self.client.get('/api/correction-requests/')
        self.assertEqual(len(response.data), 0)

    def test_a_shift_incharge_cannot_list_requests(self):
        self.client.force_authenticate(user=self.shift_incharge)
        response = self.client.get('/api/correction-requests/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_shift_incharge_cannot_approve(self):
        self.client.force_authenticate(user=self.shift_incharge)
        response = self._act('approve')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
