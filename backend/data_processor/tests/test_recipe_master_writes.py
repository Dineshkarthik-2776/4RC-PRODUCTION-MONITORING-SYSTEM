"""The two write paths into RecipeMaster must not create duplicate rows.

A recipe reaches the master two ways -- the nightly BOM sync and a manual
recipe upload -- and each used to match on the exact string. A BOM row spelled
'CP034HI' therefore created a second master row beside an existing
'CP 034 HI', giving one recipe two target speeds and two recipe types.

Both paths now match on the normalised code while keeping the human spelling.
"""

from django.test import TestCase

from data_processor.models import MaterialMaster, RecipeMaster
from data_processor.recipe_codes import (
    find_master_by_code,
    normalize_recipe_code,
    upsert_master_recipe,
)

from .base import IntegrationTestCase


class NormalizeRecipeCodeTests(TestCase):

    def test_matching_ignores_spaces_and_case(self):
        for variant in ('CP 034 HI', 'CP034HI', 'cp 034 hi', '  cp034hi  '):
            with self.subTest(variant=variant):
                self.assertEqual(normalize_recipe_code(variant), 'CP034HI')


class FindMasterByCodeTests(TestCase):

    def setUp(self):
        self.master = RecipeMaster.objects.create(
            recipe_code='CP 034 HI', target_speed=20.0, recipe_type='steel')

    def test_exact_spelling_matches(self):
        self.assertEqual(find_master_by_code('CP 034 HI'), self.master)

    def test_unspaced_spelling_matches(self):
        self.assertEqual(find_master_by_code('CP034HI'), self.master)

    def test_lowercase_spelling_matches(self):
        self.assertEqual(find_master_by_code('cp 034 hi'), self.master)

    def test_unknown_code_returns_none(self):
        self.assertIsNone(find_master_by_code('NOSUCHRECIPE'))

    def test_empty_code_returns_none(self):
        self.assertIsNone(find_master_by_code(''))
        self.assertIsNone(find_master_by_code(None))


class UpsertMasterRecipeTests(TestCase):

    def test_creates_when_absent_keeping_the_given_spelling(self):
        row, created = upsert_master_recipe(
            'ECOPOLY 350', defaults={'target_speed': 25.0, 'recipe_type': 'fabric'})
        self.assertTrue(created)
        self.assertEqual(row.recipe_code, 'ECOPOLY 350')

    def test_variant_spelling_updates_instead_of_duplicating(self):
        upsert_master_recipe(
            'CP 034 HI', defaults={'target_speed': 20.0, 'recipe_type': 'steel'})

        row, created = upsert_master_recipe(
            'CP034HI', defaults={'target_speed': 30.0, 'recipe_type': 'steel'})

        self.assertFalse(created)
        self.assertEqual(RecipeMaster.objects.count(), 1)
        self.assertEqual(row.recipe_code, 'CP 034 HI', 'existing spelling must win')
        self.assertEqual(row.target_speed, 30.0)

    def test_update_existing_false_leaves_values_alone(self):
        upsert_master_recipe(
            'CP 034 HI', defaults={'target_speed': 20.0, 'recipe_type': 'steel'})

        row, created = upsert_master_recipe(
            'CP034HI', defaults={'target_speed': 0.0, 'recipe_type': 'unknown'},
            update_existing=False)

        self.assertFalse(created)
        self.assertEqual(row.target_speed, 20.0, 'BOM sync must not reset a known speed')
        self.assertEqual(row.recipe_type, 'steel')

    def test_blank_code_creates_nothing(self):
        row, created = upsert_master_recipe('   ', defaults={'target_speed': 1.0})
        self.assertIsNone(row)
        self.assertFalse(created)
        self.assertEqual(RecipeMaster.objects.count(), 0)


class BomSyncTests(IntegrationTestCase):
    """sync_recipe_master_from_bom writes bom.FormulaCode into RecipeMaster."""

    def _bom(self, table_id, code, sap='SAP-1', equipment='4RC'):
        return MaterialMaster.objects.create(
            table_id=table_id, recipe_code=code, sap_code=sap, equipment=equipment)

    def test_bom_row_creates_a_master_recipe(self):
        from data_processor.tasks import sync_recipe_master_from_bom
        self._bom(1, 'CP 034 HI')

        sync_recipe_master_from_bom()

        self.assertTrue(RecipeMaster.objects.filter(recipe_code='CP 034 HI').exists())

    def test_bom_variant_spelling_does_not_duplicate_an_existing_master(self):
        from data_processor.tasks import sync_recipe_master_from_bom
        RecipeMaster.objects.create(
            recipe_code='CP 034 HI', target_speed=20.0, recipe_type='steel')
        self._bom(1, 'CP034HI')

        sync_recipe_master_from_bom()

        self.assertEqual(RecipeMaster.objects.count(), 1)
        row = RecipeMaster.objects.get()
        self.assertEqual(row.recipe_code, 'CP 034 HI')
        self.assertEqual(row.target_speed, 20.0,
                         'sync must not reset a speed an operator already set')

    def test_only_4rc_equipment_is_synced(self):
        from data_processor.tasks import sync_recipe_master_from_bom
        self._bom(1, 'OTHER RECIPE', equipment='3RC')

        sync_recipe_master_from_bom()

        self.assertEqual(RecipeMaster.objects.count(), 0)

    def test_sync_is_idempotent(self):
        from data_processor.tasks import sync_recipe_master_from_bom
        self._bom(1, 'CP 034 HI')

        sync_recipe_master_from_bom()
        sync_recipe_master_from_bom()

        self.assertEqual(RecipeMaster.objects.count(), 1)

    def test_two_bom_rows_differing_only_in_spacing_collapse_to_one(self):
        from data_processor.tasks import sync_recipe_master_from_bom
        self._bom(1, 'CP 034 HI')
        self._bom(2, 'CP034HI')

        sync_recipe_master_from_bom()

        self.assertEqual(RecipeMaster.objects.count(), 1,
                         'one recipe must not become two master rows')
