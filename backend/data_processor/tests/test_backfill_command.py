"""backfill_recipe_spelling: realigning historical summary rows.

Covers the rows written while the pipeline stored the normalised code, which
the dashboard's recipe filter cannot match.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from data_processor.models import ChangeoverSummary, RecipeMaster


def run(*args):
    out = StringIO()
    call_command('backfill_recipe_spelling', *args, stdout=out, stderr=out)
    return out.getvalue()


class BackfillRecipeSpellingTests(TestCase):

    def setUp(self):
        RecipeMaster.objects.create(
            recipe_code='CP 034 HI', target_speed=20.0, recipe_type='steel')
        RecipeMaster.objects.create(
            recipe_code='ECOPOLY 350', target_speed=25.0, recipe_type='fabric')
        RecipeMaster.objects.create(
            recipe_code='BRTTL', target_speed=40.0, recipe_type='fabric')

    def test_normalised_codes_are_rewritten_to_master_spelling(self):
        summary = ChangeoverSummary.objects.create(
            batch=1, current_recipe='CP034HI', previous_recipe='ECOPOLY350')

        run()

        summary.refresh_from_db()
        self.assertEqual(summary.current_recipe, 'CP 034 HI')
        self.assertEqual(summary.previous_recipe, 'ECOPOLY 350')

    def test_rows_already_correct_are_left_alone(self):
        ChangeoverSummary.objects.create(
            batch=2, current_recipe='CP 034 HI', previous_recipe='BRTTL')

        output = run()

        self.assertIn('Nothing to change', output)

    def test_lowercase_and_spacing_variants_both_resolve(self):
        summary = ChangeoverSummary.objects.create(
            batch=3, current_recipe='cp 034 hi', previous_recipe='brttl')

        run()

        summary.refresh_from_db()
        self.assertEqual(summary.current_recipe, 'CP 034 HI')
        self.assertEqual(summary.previous_recipe, 'BRTTL')

    def test_dry_run_writes_nothing(self):
        summary = ChangeoverSummary.objects.create(
            batch=4, current_recipe='CP034HI', previous_recipe='BRTTL')

        output = run('--dry-run')

        summary.refresh_from_db()
        self.assertEqual(summary.current_recipe, 'CP034HI',
                         'dry run modified the database')
        self.assertIn('DRY RUN', output)
        self.assertIn('1 row(s) would be updated', output)

    def test_codes_with_no_master_row_are_left_unchanged_and_reported(self):
        summary = ChangeoverSummary.objects.create(
            batch=5, current_recipe='GHOSTRECIPE', previous_recipe='BRTTL')

        output = run()

        summary.refresh_from_db()
        self.assertEqual(summary.current_recipe, 'GHOSTRECIPE')
        self.assertIn('no RecipeMaster row', output)

    def test_null_recipe_fields_do_not_crash(self):
        ChangeoverSummary.objects.create(
            batch=6, current_recipe=None, previous_recipe=None)
        run()  # must not raise

    def test_command_is_idempotent(self):
        summary = ChangeoverSummary.objects.create(
            batch=7, current_recipe='CP034HI', previous_recipe='ECOPOLY350')

        run()
        first = (summary.current_recipe, summary.previous_recipe)
        summary.refresh_from_db()
        after_first = (summary.current_recipe, summary.previous_recipe)

        second_output = run()
        summary.refresh_from_db()

        self.assertEqual(
            (summary.current_recipe, summary.previous_recipe), after_first)
        self.assertIn('Nothing to change', second_output)
        self.assertNotEqual(first, after_first)

    def test_aborts_when_recipe_master_is_empty(self):
        RecipeMaster.objects.all().delete()
        ChangeoverSummary.objects.create(batch=8, current_recipe='CP034HI')

        output = run()

        self.assertIn('RecipeMaster is empty', output)
