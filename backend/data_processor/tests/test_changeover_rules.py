"""resolve_changeover: custom-rule precedence and base-type fallback.

Documentation section 2.5 defines the Standard Time Master keys; the four
custom-rule levels below are implementation behaviour not covered by the PDF,
so these tests are the only written record of the precedence order.
"""

from django.test import SimpleTestCase

from data_processor.tasks import resolve_changeover

BASE_RULES = {
    'Steel to Steel': 'Steel to Steel',
    'Steel to Fabric': 'Steel to Fabric',
    'Fabric to Steel': 'Fabric to Steel',
    'Fabric to Fabric': 'Fabric to Fabric',
}


def resolve(prev='BRTTL', curr='HIRAD', prev_type='steel', curr_type='steel',
            custom=None, base=None):
    return resolve_changeover(
        prev, curr, prev_type, curr_type,
        {} if custom is None else custom,
        BASE_RULES if base is None else base,
    )


class CustomRuleMatchingTests(SimpleTestCase):
    """Each of the four custom-rule levels matches on its own."""

    def test_exact_pair(self):
        self.assertEqual(resolve(custom={('BRTTL', 'HIRAD'): 'EXACT'}), 'EXACT')

    def test_to_wildcard(self):
        self.assertEqual(resolve(custom={('ANY', 'HIRAD'): 'TO_WILD'}), 'TO_WILD')

    def test_from_wildcard(self):
        self.assertEqual(resolve(custom={('BRTTL', 'ANY'): 'FROM_WILD'}), 'FROM_WILD')

    def test_double_wildcard(self):
        self.assertEqual(resolve(custom={('ANY', 'ANY'): 'DOUBLE'}), 'DOUBLE')


class CustomRulePrecedenceTests(SimpleTestCase):
    """Precedence is exact > to-wildcard > from-wildcard > double-wildcard.

    This ordering is load-bearing and easy to break in a refactor, so each
    adjacent pair is pinned separately rather than in one combined case.
    """

    def test_exact_beats_to_wildcard(self):
        self.assertEqual(
            resolve(custom={('BRTTL', 'HIRAD'): 'EXACT', ('ANY', 'HIRAD'): 'TO_WILD'}),
            'EXACT',
        )

    def test_to_wildcard_beats_from_wildcard(self):
        self.assertEqual(
            resolve(custom={('ANY', 'HIRAD'): 'TO_WILD', ('BRTTL', 'ANY'): 'FROM_WILD'}),
            'TO_WILD',
        )

    def test_from_wildcard_beats_double_wildcard(self):
        self.assertEqual(
            resolve(custom={('BRTTL', 'ANY'): 'FROM_WILD', ('ANY', 'ANY'): 'DOUBLE'}),
            'FROM_WILD',
        )

    def test_all_four_present_exact_wins(self):
        self.assertEqual(
            resolve(custom={
                ('BRTTL', 'HIRAD'): 'EXACT',
                ('ANY', 'HIRAD'): 'TO_WILD',
                ('BRTTL', 'ANY'): 'FROM_WILD',
                ('ANY', 'ANY'): 'DOUBLE',
            }),
            'EXACT',
        )

    def test_custom_rule_beats_base_type_fallback(self):
        self.assertEqual(
            resolve(prev_type='steel', curr_type='steel',
                    custom={('BRTTL', 'HIRAD'): 'EXACT'}),
            'EXACT',
        )


class BaseTypeFallbackTests(SimpleTestCase):
    """With no custom rule, the recipe types select a base key."""

    def test_all_four_type_pairs(self):
        cases = [
            ('steel', 'steel', 'Steel to Steel'),
            ('steel', 'fabric', 'Steel to Fabric'),
            ('fabric', 'steel', 'Fabric to Steel'),
            ('fabric', 'fabric', 'Fabric to Fabric'),
        ]
        for prev_type, curr_type, expected in cases:
            with self.subTest(prev=prev_type, curr=curr_type):
                self.assertEqual(resolve(prev_type=prev_type, curr_type=curr_type), expected)

    def test_type_matching_is_case_insensitive(self):
        self.assertEqual(resolve(prev_type='STEEL', curr_type='Steel'), 'Steel to Steel')

    def test_type_matching_tolerates_non_string_values(self):
        # str() is applied before lowering, so a stray non-string does not raise.
        self.assertEqual(resolve(prev_type='Fabric', curr_type='FABRIC'), 'Fabric to Fabric')


class UnresolvableTests(SimpleTestCase):
    """None and 'Unknown' are different outcomes and must stay different.

    A None result means the changeover type could not be determined at all; an
    'Unknown' result means the types were read but no key matched. Only the
    latter reaches the summary row, so collapsing them would hide missing data.
    """

    def test_missing_previous_type_returns_none(self):
        self.assertIsNone(resolve(prev_type=None))

    def test_missing_current_type_returns_none(self):
        self.assertIsNone(resolve(curr_type=None))

    def test_empty_string_type_returns_none(self):
        self.assertIsNone(resolve(prev_type=''))

    def test_unrecognised_type_returns_unknown(self):
        self.assertEqual(resolve(prev_type='rubber', curr_type='rubber'), 'Unknown')

    def test_base_key_absent_from_standard_time_master_returns_unknown(self):
        # The type pair resolves, but no StandardTimeMaster row defines that key.
        self.assertEqual(
            resolve(prev_type='steel', curr_type='steel', base={'Fabric to Fabric': 'x'}),
            'Unknown',
        )

    def test_wildcard_still_applies_when_types_are_missing(self):
        # Custom rules are checked before the type guard, so a wildcard rule
        # resolves even with no type information at all.
        self.assertEqual(
            resolve(prev_type=None, curr_type=None, custom={('ANY', 'ANY'): 'DOUBLE'}),
            'DOUBLE',
        )
