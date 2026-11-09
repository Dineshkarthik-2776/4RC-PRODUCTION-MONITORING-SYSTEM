"""The User model: identity, roles, and the account lifecycle."""

from django.test import TestCase, override_settings

from user_authentication.models import User, normalize_employee_id

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


class NormalizeEmployeeIdTests(TestCase):
    """IDs are typed by hand and arrive from spreadsheets in any shape."""

    def test_spacing_and_case_collapse(self):
        for variant in ('10432', ' 10432 ', 'jk 10432', 'JK10432'):
            with self.subTest(variant=variant):
                self.assertEqual(
                    normalize_employee_id(variant),
                    variant.strip().replace(' ', '').upper())

    def test_none_and_blank_become_empty(self):
        self.assertEqual(normalize_employee_id(None), '')
        self.assertEqual(normalize_employee_id('   '), '')

    def test_leading_zeros_are_preserved(self):
        # Employee IDs are stored as text, so 004321 keeps its zeros rather
        # than becoming 4321.
        self.assertEqual(normalize_employee_id('004321'), '004321')


@fast_passwords
class RosterAccountTests(TestCase):
    """Roster accounts start unusable and are activated by being claimed."""

    def setUp(self):
        self.user = User.objects.create_roster_user(
            employee_id='10432', name='A Kumar', role=User.Role.OPERATOR)

    def test_starts_pending_and_inactive(self):
        self.assertEqual(self.user.status, User.Status.PENDING)
        self.assertFalse(self.user.is_active)

    def test_starts_with_no_usable_password(self):
        self.assertFalse(self.user.has_usable_password())

    def test_is_claimable(self):
        self.assertTrue(self.user.can_claim)

    def test_claiming_activates_the_account(self):
        self.user.claim('Plant2026!')

        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.ACTIVE)
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.check_password('Plant2026!'))

    def test_a_claimed_account_cannot_be_claimed_again(self):
        self.user.claim('Plant2026!')
        self.user.refresh_from_db()

        self.assertFalse(self.user.can_claim)
        with self.assertRaises(ValueError):
            self.user.claim('Someone2026!')

    def test_employee_id_is_normalised_on_creation(self):
        user = User.objects.create_roster_user(
            employee_id=' jk 777 ', name='X', role=User.Role.OPERATOR)
        self.assertEqual(user.employee_id, 'JK777')

    def test_blank_employee_id_is_rejected(self):
        with self.assertRaises(ValueError):
            User.objects.create_roster_user(employee_id='  ', name='X')


@fast_passwords
class RetiredAccountTests(TestCase):

    def test_retiring_makes_the_account_unusable(self):
        user = User.objects.create_user(
            employee_id='10432', password='Plant2026!', name='A Kumar')

        user.retire()

        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.RETIRED)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())

    def test_a_retired_account_is_not_claimable(self):
        # It must not be possible to take over a retired identity by
        # "claiming" it.
        user = User.objects.create_roster_user(employee_id='10432', name='A')
        user.retire()
        self.assertFalse(user.can_claim)


@fast_passwords
class RoleTests(TestCase):

    def test_there_are_exactly_three_roles(self):
        self.assertEqual(
            [r for r, _ in User.Role.choices],
            ['manager', 'shift_incharge', 'operator'])

    def test_default_role_is_operator(self):
        # The least-privileged role, so a roster row with a missing role
        # cannot accidentally grant write access.
        user = User.objects.create_roster_user(employee_id='1', name='X')
        self.assertEqual(user.role, User.Role.OPERATOR)

    def test_is_manager_is_true_only_for_managers(self):
        cases = [
            (User.Role.MANAGER, True),
            (User.Role.SHIFT_INCHARGE, False),
            (User.Role.OPERATOR, False),
        ]
        for index, (role, expected) in enumerate(cases):
            with self.subTest(role=role):
                user = User.objects.create_user(
                    employee_id=f'E{index}', password='x', role=role)
                self.assertEqual(user.is_manager, expected)


@fast_passwords
class SuperuserTests(TestCase):
    """An administrator is a manager carrying Django's privilege flags."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            employee_id='10001', password='Admin2026!', name='P Mehta')

    def test_is_a_manager_not_a_separate_role(self):
        self.assertEqual(self.admin.role, User.Role.MANAGER)

    def test_carries_the_django_flags(self):
        self.assertTrue(self.admin.is_staff)
        self.assertTrue(self.admin.is_superuser)

    def test_is_active_immediately(self):
        # A superuser is created directly, not claimed from a roster.
        self.assertTrue(self.admin.is_active)
        self.assertEqual(self.admin.status, User.Status.ACTIVE)

    def test_username_field_is_the_employee_id(self):
        self.assertEqual(User.USERNAME_FIELD, 'employee_id')
        self.assertEqual(self.admin.get_username(), '10001')
