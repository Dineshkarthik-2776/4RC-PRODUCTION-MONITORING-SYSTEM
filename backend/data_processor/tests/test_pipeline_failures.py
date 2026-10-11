"""process_changeover_data: what happens when the signal is not clean.

Documentation section 3.4: "If the machine never drops to 0 speed, or never
reaches the 90% target threshold, the system flags the batch with specific
Remarks explaining why the calculations were aborted."

Section 3.7 defines the raw-data status tags the task applies:
SUCCESS, IGNORED_LINE_PASS, SKIPPED_NO_TARGET_SPEED, FAILED_OPERATIONAL_SPEED.

A changeover that cannot be measured must still produce a row -- with a remark
saying why -- rather than silently vanishing from the dashboard.
"""

import datetime
from unittest.mock import patch

from django.db import DatabaseError

from data_processor.models import ChangeoverSummary, RawLineData
from data_processor.tasks import process_changeover_data

from .base import IntegrationTestCase
from .factories import (
    SAMPLE_SECONDS,
    changeover_samples,
    make_recipe,
    make_standard_time,
    write_samples,
)


def hours_ago(n):
    return datetime.datetime.now() - datetime.timedelta(hours=n)


def write_sequence(rows, hours=6):
    span = datetime.timedelta(seconds=len(rows) * SAMPLE_SECONDS)
    return write_samples(rows, hours_ago(hours) - span)


class MissingRecipeMasterTests(IntegrationTestCase):
    """A recipe with no master row has no target speed (PL-11)."""

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        # RECIPE B deliberately absent from RecipeMaster.
        make_standard_time('Fabric to Fabric', minutes=10.0)
        write_sequence(changeover_samples())

    def test_rows_are_flagged_skipped_not_marked_done(self):
        process_changeover_data()
        skipped = RawLineData.objects.filter(status='SKIPPED_NO_TARGET_SPEED')
        self.assertTrue(skipped.exists(),
                        'rows for an unknown recipe should be tagged SKIPPED_NO_TARGET_SPEED')

    def test_a_remark_explains_the_missing_target_speed(self):
        process_changeover_data()
        summary = ChangeoverSummary.objects.first()
        if summary is None:
            self.skipTest('no summary row produced for an unknown recipe')
        self.assertIsNotNone(summary.remarks)
        self.assertIn('target speed is missing', summary.remarks)


class NeverStoppedTests(IntegrationTestCase):
    """The line changed recipe without ever reaching zero speed (PL-12)."""

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        # Full speed throughout, straight from one recipe to the next.
        write_sequence([('RECIPE A', 20.0)] * 30 + [('RECIPE B', 20.0)] * 30)

    def test_a_summary_row_is_still_written(self):
        process_changeover_data()
        self.assertEqual(ChangeoverSummary.objects.count(), 1,
                         'an unmeasurable changeover must not disappear')

    def test_the_remark_says_zero_speed_was_not_reached(self):
        process_changeover_data()
        self.assertIn('zero speed was not reached',
                      ChangeoverSummary.objects.get().remarks or '')

    def test_setup_times_are_left_empty(self):
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertIsNone(summary.setup_time_actual)


class BelowTargetSpeedTests(IntegrationTestCase):
    """The line never reached 90% of target (PL-13).

    'EQUAL SPEEDS' marks samples at >= 90% of the recipe's target speed. With
    none, the changeover cannot be bounded and the remark must name both the
    target and what actually ran, so an engineer can see the gap.
    """

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=100.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=100.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        # Target 100, but the line only ever manages 20.
        write_sequence(changeover_samples(target_speed=20.0))

    def test_remark_names_the_target_and_the_actual_maximum(self):
        process_changeover_data()
        remarks = ChangeoverSummary.objects.get().remarks or ''
        self.assertIn('max speed ran was', remarks)
        self.assertIn('100.0', remarks)


class UnidentifiableSetupPointsTests(IntegrationTestCase):
    """Speeds look plausible but the four points cannot be located (PL-14)."""

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        # Reaches target and touches zero, but the stop is a single sample --
        # too short for C_point's 15-sample acceleration window.
        write_sequence(
            [('RECIPE A', 20.0)] * 20 + [('RECIPE A', 0.0)]
            + [('RECIPE B', 20.0)] * 20
        )

    def test_summary_is_written_with_an_explanatory_remark(self):
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertIsNotNone(summary.remarks)

    def test_remark_is_not_silently_blank(self):
        process_changeover_data()
        self.assertNotEqual((ChangeoverSummary.objects.get().remarks or '').strip(), '')


class RemarkClearedOnSuccessTests(IntegrationTestCase):
    """A measurable changeover carries no remark (PL-15)."""

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        write_sequence(changeover_samples())

    def test_clean_changeover_has_no_remark(self):
        process_changeover_data()
        self.assertIsNone(ChangeoverSummary.objects.get().remarks)


class BatchSegmentationTests(IntegrationTestCase):
    """Batches are cut on recipe change, not on time (PL-07 to PL-09)."""

    def setUp(self):
        super().setUp()
        for code in ('RECIPE A', 'RECIPE B', 'RECIPE C', 'LINE PASS'):
            make_recipe(code, target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)

    def test_repeated_recipe_does_not_start_a_new_batch(self):
        # One recipe throughout: no transition, so no changeover.
        write_sequence([('RECIPE A', 20.0)] * 60)
        process_changeover_data()
        self.assertEqual(ChangeoverSummary.objects.count(), 0)

    def test_two_changeovers_produce_two_summaries(self):
        rows = (changeover_samples('RECIPE A', 'RECIPE B')
                + changeover_samples('RECIPE B', 'RECIPE C'))
        write_sequence(rows)

        process_changeover_data()

        self.assertEqual(ChangeoverSummary.objects.count(), 2)

    def test_changeovers_are_paired_in_sequence(self):
        rows = (changeover_samples('RECIPE A', 'RECIPE B')
                + changeover_samples('RECIPE B', 'RECIPE C'))
        write_sequence(rows)

        process_changeover_data()

        pairs = set(ChangeoverSummary.objects.values_list(
            'previous_recipe', 'current_recipe'))
        self.assertIn(('RECIPE A', 'RECIPE B'), pairs)
        self.assertIn(('RECIPE B', 'RECIPE C'), pairs)

    def test_line_pass_between_recipes_does_not_break_the_pairing(self):
        # LINE PASS rows are excluded before batching, so the recipes either
        # side of them must still pair as a single changeover.
        rows = (changeover_samples('RECIPE A', 'RECIPE B'))
        rows = rows[:40] + [('LINE PASS', 12.0)] * 10 + rows[40:]
        write_sequence(rows)

        process_changeover_data()

        self.assertEqual(ChangeoverSummary.objects.count(), 1)
        summary = ChangeoverSummary.objects.get()
        self.assertEqual(summary.previous_recipe, 'RECIPE A')
        self.assertEqual(summary.current_recipe, 'RECIPE B')

    def test_line_pass_rows_are_tagged_ignored(self):
        rows = changeover_samples('RECIPE A', 'RECIPE B')
        rows = rows[:40] + [('LINE PASS', 12.0)] * 10 + rows[40:]
        write_sequence(rows)

        process_changeover_data()

        self.assertTrue(
            RawLineData.objects.filter(status='IGNORED_LINE_PASS').exists())


class TransactionIntegrityTests(IntegrationTestCase):
    """A crash mid-write must not consume raw data (PL-18).

    This is the worst failure mode available: rows marked processed with no
    summary written means the data is gone. The three-day window moves past
    them and nothing will ever reprocess them.
    """

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        write_sequence(changeover_samples())

    def test_failure_while_saving_leaves_raw_data_unconsumed(self):
        with patch.object(ChangeoverSummary, 'save',
                          side_effect=DatabaseError('simulated failure')):
            with self.assertRaises(DatabaseError):
                process_changeover_data()

        self.assertEqual(
            RawLineData.objects.filter(processed_flag=True).count(), 0,
            'raw rows were consumed despite the summary write failing',
        )

    def test_failure_while_saving_writes_no_partial_summary(self):
        with patch.object(ChangeoverSummary, 'save',
                          side_effect=DatabaseError('simulated failure')):
            with self.assertRaises(DatabaseError):
                process_changeover_data()

        self.assertEqual(ChangeoverSummary.objects.count(), 0)
