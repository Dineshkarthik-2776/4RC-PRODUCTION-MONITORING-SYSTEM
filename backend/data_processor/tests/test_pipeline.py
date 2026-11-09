"""process_changeover_data: end-to-end pipeline behaviour.

These run against a throwaway test database with Celery forced inline -- see
data_processor/tests/base.py for the guarantees.
"""

import datetime

from data_processor.models import ChangeoverSummary, RawLineData
from data_processor.tasks import process_changeover_data

from .base import IntegrationTestCase
from .factories import (
    build_changeover,
    make_recipe,
    make_standard_time,
    write_samples,
)


class EmptyInputTests(IntegrationTestCase):
    """The task must be a safe no-op when there is nothing to do."""

    def test_no_rows_at_all(self):
        result = process_changeover_data()
        self.assertEqual(ChangeoverSummary.objects.count(), 0)
        self.assertIn('No new data', result)

    def test_rows_older_than_three_days_are_ignored(self):
        make_recipe('RECIPE A')
        make_recipe('RECIPE B')
        old = datetime.datetime.now() - datetime.timedelta(days=5)
        write_samples([('RECIPE A', 20.0)] * 10, old)

        process_changeover_data()

        self.assertEqual(ChangeoverSummary.objects.count(), 0)
        # Outside the window, so they are not consumed either.
        self.assertEqual(
            RawLineData.objects.filter(processed_flag=False).count(), 10)

    def test_already_processed_rows_are_ignored(self):
        make_recipe('RECIPE A')
        write_samples([('RECIPE A', 20.0)] * 10,
                      datetime.datetime.now() - datetime.timedelta(hours=1),
                      processed=True)

        result = process_changeover_data()
        self.assertIn('No new data', result)


class LinePassFilteringTests(IntegrationTestCase):
    """LINE PASS rows are excluded from changeover detection (section 3.7)."""

    def test_only_line_pass_rows_produces_no_summary(self):
        make_recipe('LINE PASS')
        write_samples([('LINE PASS', 15.0)] * 20,
                      datetime.datetime.now() - datetime.timedelta(hours=2))

        process_changeover_data()

        self.assertEqual(ChangeoverSummary.objects.count(), 0)

    def test_line_pass_rows_are_marked_processed_not_left_behind(self):
        make_recipe('LINE PASS')
        write_samples([('LINE PASS', 15.0)] * 20,
                      datetime.datetime.now() - datetime.timedelta(hours=2))

        process_changeover_data()

        self.assertEqual(
            RawLineData.objects.filter(processed_flag=False).count(), 0,
            'LINE PASS rows must not be reprocessed on every run',
        )


class CompleteChangeoverTests(IntegrationTestCase):
    """A clean signal produces one summary with the derived values."""

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        build_changeover()

    def test_one_summary_row_is_created(self):
        process_changeover_data()
        self.assertEqual(ChangeoverSummary.objects.count(), 1)

    def test_recipes_are_paired_in_the_right_direction(self):
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertEqual(summary.previous_recipe, 'RECIPE A')
        self.assertEqual(summary.current_recipe, 'RECIPE B')

    def test_summary_stores_the_master_spelling(self):
        # Codes are normalised for MATCHING (sensors send 'CPJ120', 'CPJ 120'
        # and 'cpj 120' for the same recipe), but the summary stores the
        # spelling RecipeMaster holds.
        #
        # That matters because the UI builds its recipe dropdown from
        # RecipeMaster and filters ChangeoverSummary with it. If the summary
        # held the normalised form, the dropdown value would never match and
        # the filter would silently return nothing.
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertEqual(summary.current_recipe, 'RECIPE B')
        self.assertEqual(summary.previous_recipe, 'RECIPE A')

    def test_changeover_type_resolves_from_recipe_types(self):
        process_changeover_data()
        self.assertEqual(
            ChangeoverSummary.objects.get().change_over_type, 'Fabric to Fabric')

    def test_setup_times_are_computed(self):
        # The whole point of the pipeline: a clean signal must yield numbers,
        # not just a row. Without this, a regression that silently returns
        # NaN for every changeover would still pass the other tests here.
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertIsNotNone(summary.setup_time_actual)
        self.assertGreater(summary.setup_time_actual, 0)
        self.assertIsNotNone(summary.static_setup_time)
        self.assertIsNotNone(summary.ramp_up_time)
        self.assertAlmostEqual(
            summary.setup_time_actual,
            summary.static_setup_time + summary.ramp_up_time,
            places=1,
        )

    def test_clean_changeover_carries_no_remark(self):
        process_changeover_data()
        self.assertIsNone(ChangeoverSummary.objects.get().remarks)

    def test_standard_time_is_snapshotted_from_the_master(self):
        process_changeover_data()
        self.assertEqual(ChangeoverSummary.objects.get().standard_time, 10)

    def test_shift_and_production_date_are_populated(self):
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertIn(summary.shift, {'A', 'B', 'C'})
        self.assertIsNotNone(summary.production_date)

    def test_consumed_rows_are_marked_with_a_status(self):
        process_changeover_data()
        self.assertTrue(
            RawLineData.objects.filter(status='SUCCESS').exists(),
            'rows feeding a completed changeover should be tagged SUCCESS',
        )

    def test_the_still_running_batch_is_left_unprocessed(self):
        # Intentional, and easy to mistake for an idempotency bug.
        #
        # The task never consumes the newest batch: more samples for it may
        # still arrive, so marking it done would freeze an in-progress run.
        # Only batches strictly older than the last one are finalised
        # (tasks.py, `raw_df['BATCH'] < last_bat`).
        #
        # The consequence is that every run re-reads the trailing batch, which
        # is why idempotency has to be enforced at the summary level rather
        # than by processed_flag alone.
        process_changeover_data()
        remaining = RawLineData.objects.filter(processed_flag=False)
        self.assertGreater(remaining.count(), 0)
        self.assertEqual(
            set(remaining.values_list('recipe_code', flat=True)), {'RECIPE B'},
            'only the newest batch should remain unprocessed',
        )


class IdempotencyTests(IntegrationTestCase):
    """The single most important behaviour in the pipeline.

    The task is on a five-minute Celery beat. If a second run over the same
    data created a second summary, production records would duplicate
    continuously and silently.
    """

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)
        build_changeover()

    def test_running_twice_does_not_duplicate_summaries(self):
        process_changeover_data()
        first = ChangeoverSummary.objects.count()

        process_changeover_data()

        self.assertEqual(ChangeoverSummary.objects.count(), first,
                         'second run duplicated changeover records')

    def test_rerun_reprocesses_only_the_trailing_batch(self):
        process_changeover_data()
        before = set(RawLineData.objects.filter(processed_flag=False)
                     .values_list('id', flat=True))

        process_changeover_data()
        after = set(RawLineData.objects.filter(processed_flag=False)
                    .values_list('id', flat=True))

        # The trailing batch is re-read each run by design; what must not
        # happen is previously finalised rows coming back.
        self.assertTrue(after.issubset(before),
                        'rows that were already finalised became unprocessed again')

    def test_summary_values_are_stable_across_runs(self):
        process_changeover_data()
        first = ChangeoverSummary.objects.get()
        snapshot = (first.previous_recipe, first.current_recipe,
                    first.change_over_type, first.shift)

        process_changeover_data()
        second = ChangeoverSummary.objects.get()

        self.assertEqual(
            (second.previous_recipe, second.current_recipe,
             second.change_over_type, second.shift),
            snapshot,
            'a second run rewrote the changeover with different values',
        )


class RecipeSpellingTests(IntegrationTestCase):
    """Sensor data arrives in several spellings; the summary must settle on one.

    This is the regression guard for the dashboard's recipe filter, which
    compares a RecipeMaster dropdown value against ChangeoverSummary.
    """

    def setUp(self):
        super().setUp()
        make_recipe('RECIPE A', target_speed=20.0, recipe_type='fabric')
        make_recipe('RECIPE B', target_speed=20.0, recipe_type='fabric')
        make_standard_time('Fabric to Fabric', minutes=10.0)

    def _run_with_sensor_spelling(self, previous, current):
        build_changeover(previous_recipe=previous, current_recipe=current)
        process_changeover_data()
        return ChangeoverSummary.objects.get()

    def test_spaced_sensor_codes_resolve_to_the_master_spelling(self):
        summary = self._run_with_sensor_spelling('RECIPE A', 'RECIPE B')
        self.assertEqual(summary.current_recipe, 'RECIPE B')

    def test_unspaced_sensor_codes_resolve_to_the_master_spelling(self):
        # The sensor wrote 'RECIPEB'; the master says 'RECIPE B'.
        summary = self._run_with_sensor_spelling('RECIPEA', 'RECIPEB')
        self.assertEqual(summary.current_recipe, 'RECIPE B')

    def test_lowercase_sensor_codes_resolve_to_the_master_spelling(self):
        summary = self._run_with_sensor_spelling('recipe a', 'recipe b')
        self.assertEqual(summary.current_recipe, 'RECIPE B')

    def test_every_spelling_matches_the_ui_dropdown_value(self):
        # What the dashboard actually does:
        #   detail.current_recipe.includes(selectedRecipe)
        # with selectedRecipe taken from the RecipeMaster dropdown.
        summary = self._run_with_sensor_spelling('RECIPEA', 'recipe b')
        dropdown_value = 'RECIPE B'
        self.assertIn(dropdown_value, summary.current_recipe)

    def test_recipe_absent_from_master_keeps_its_own_code(self):
        # No master row for the incoming recipe, so there is no canonical
        # spelling to adopt. The row must still be written, not dropped.
        build_changeover(previous_recipe='RECIPE A', current_recipe='UNKNOWN X')
        process_changeover_data()
        summary = ChangeoverSummary.objects.get()
        self.assertIsNotNone(summary.current_recipe)
        self.assertIn('UNKNOWN', summary.current_recipe.upper())
