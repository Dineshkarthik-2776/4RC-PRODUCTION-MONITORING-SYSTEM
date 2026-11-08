"""Operator attribution as the pipeline actually produces it.

test_setup_allocation.py checks the arithmetic and test_setup_capture_api.py
checks it against hand-built rows. This file runs the real task over real
raw samples, which is the only place the pipeline's own timestamps -- pandas
Timestamps, sometimes timezone-aware -- reach the allocation code.
"""

import datetime

from data_processor.models import ChangeoverSummary, SetupAllocation
from data_processor.setup_capture import as_naive_local, open_duty_session
from data_processor.tasks import process_changeover_data
from user_authentication.models import User

from .base import IntegrationTestCase, fast_passwords
from .factories import build_changeover, make_recipe


@fast_passwords
class PipelineAttributionTests(IntegrationTestCase):

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A')
        make_recipe('RECIPE B')

    def make_operator(self, employee_id='OP1'):
        return User.objects.create_user(
            employee_id=employee_id, password='line-pass-99',
            name='Line Operator', role=User.Role.OPERATOR)

    def test_a_processed_changeover_is_allocated(self):
        operator = self.make_operator()
        # On duty well before the samples begin.
        open_duty_session(
            operator, at=datetime.datetime.now() - datetime.timedelta(days=1))
        build_changeover()

        process_changeover_data()

        summary = ChangeoverSummary.objects.get()
        self.assertTrue(summary.allocations.exists())
        self.assertEqual(
            {row.operator for row in summary.allocations.all()}, {operator})

    def test_the_pipelines_aware_timestamps_do_not_break_allocation(self):
        # Regression: the task hands the summary over before its fields
        # have been through LocalTimeDateTimeField, so the timestamps are
        # still pandas values and can be timezone-aware. Comparing one of
        # those against a naive shift boundary used to raise TypeError and
        # take the whole pipeline run down with it.
        build_changeover()

        process_changeover_data()

        self.assertTrue(SetupAllocation.objects.exists())

    def test_allocated_time_matches_the_detected_setup_window(self):
        build_changeover()

        process_changeover_data()

        summary = ChangeoverSummary.objects.get()
        window = (as_naive_local(summary.setup_complete_timestamp)
                  - as_naive_local(summary.setup_start_timestamp))
        allocated = sum(row.allocated_minutes
                        for row in summary.allocations.all())
        self.assertAlmostEqual(
            allocated, window.total_seconds() / 60.0, places=1)

    def test_a_setup_with_nobody_signed_in_is_unattributed(self):
        build_changeover()

        process_changeover_data()

        self.assertTrue(
            SetupAllocation.objects.filter(operator__isnull=True).exists())

    def test_rerunning_the_pipeline_does_not_duplicate_allocations(self):
        build_changeover()

        process_changeover_data()
        first = SetupAllocation.objects.count()
        process_changeover_data()

        self.assertEqual(SetupAllocation.objects.count(), first)
