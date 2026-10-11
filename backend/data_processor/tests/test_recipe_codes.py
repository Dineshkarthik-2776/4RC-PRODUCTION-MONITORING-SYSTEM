"""Recipe-code normalisation and timestamp coercion."""

from django.test import SimpleTestCase

from data_processor.tasks import (
    LINE_PASS_CODE,
    _normalize_recipe_code,
    to_local_ts,
)


class NormalizeRecipeCodeTests(SimpleTestCase):
    """_normalize_recipe_code strips spaces and upper-cases."""

    def test_internal_spaces_removed(self):
        self.assertEqual(_normalize_recipe_code('CPJ 120'), 'CPJ120')

    def test_surrounding_whitespace_stripped_and_upper_cased(self):
        self.assertEqual(_normalize_recipe_code('  brttl  '), 'BRTTL')

    def test_none_becomes_empty_string(self):
        self.assertEqual(_normalize_recipe_code(None), '')

    def test_empty_string_stays_empty(self):
        self.assertEqual(_normalize_recipe_code(''), '')

    def test_line_pass_matches_the_module_constant(self):
        # The pipeline filters LINE PASS rows by comparing against this constant,
        # so any drift here silently stops that filtering.
        self.assertEqual(_normalize_recipe_code('line pass'), LINE_PASS_CODE)
        self.assertEqual(_normalize_recipe_code('LINE PASS'), LINE_PASS_CODE)

    def test_zero_is_treated_as_missing(self):
        # `value or ''` makes any falsy value empty, including numeric zero.
        self.assertEqual(_normalize_recipe_code(0), '')

    def test_nan_becomes_the_literal_string_nan(self):
        # DOC MISMATCH / open question.
        # float('nan') is truthy, so `value or ''` keeps it and str() yields 'nan'.
        # A missing recipe code therefore normalises to the recipe 'NAN' rather
        # than to '' -- creating a phantom recipe that will never match
        # RecipeMaster and will be reported as a missing-recipe warning.
        # Asserting current behaviour; confirm with the team whether NaN should
        # collapse to '' instead.
        self.assertEqual(_normalize_recipe_code(float('nan')), 'NAN')


class ToLocalTsTests(SimpleTestCase):
    """to_local_ts coerces to a tz-aware Asia/Kolkata timestamp."""

    def test_naive_input_is_localised_without_shifting_the_clock(self):
        ts = to_local_ts('2026-04-07 10:30:00')
        self.assertIsNotNone(ts.tzinfo)
        self.assertEqual((ts.hour, ts.minute), (10, 30))

    def test_utc_input_is_converted_to_ist(self):
        # 05:00 UTC is 10:30 IST (+5:30).
        ts = to_local_ts('2026-04-07 05:00:00+00:00')
        self.assertEqual((ts.hour, ts.minute), (10, 30))

    def test_returns_aware_timestamps_while_the_project_stores_naive(self):
        # Context, not a defect in this function: the project runs USE_TZ=False
        # and stores naive wall-clock time, but this helper hands back AWARE
        # timestamps. calculate_time_loss compares them against DataFrame
        # columns -- see test_time_calculations.AwareNaiveComparisonTests.
        ts = to_local_ts('2026-04-07 10:30:00')
        self.assertEqual(str(ts.tzinfo), 'Asia/Kolkata')
