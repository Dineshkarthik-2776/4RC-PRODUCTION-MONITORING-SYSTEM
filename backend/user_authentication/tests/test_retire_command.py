"""retire_legacy_users: clearing the way for the employee-ID roster import.

The command branches on one number -- how many ChangeoverCorrectionRequest
rows exist -- because requested_by is the only foreign key to the user table
in the project. With none, users can be deleted outright. With any, deleting
would cascade that history away, so the accounts are kept but made unusable.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token

from data_processor.models import (
    ChangeoverCorrectionRequest,
    ChangeoverSummary,
    OvershootReasons,
)

User = get_user_model()

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


def run(*args, **kwargs):
    out = StringIO()
    call_command('retire_legacy_users', *args, stdout=out, stderr=out, **kwargs)
    return out.getvalue()


@fast_passwords
class RetireLegacyUsersBase(TestCase):

    def setUp(self):
        # These stand in for rows that survived migration 0002 with
        # LEGACY-nnnn identifiers.
        self.worker = User.objects.create_user(
            employee_id='LEGACY-0001', password='oldpass123',
            name='Old Operator', role=User.Role.OPERATOR)
        self.manager = User.objects.create_user(
            employee_id='LEGACY-0002', password='oldpass123',
            name='Old Manager', role=User.Role.MANAGER)
        self.admin = User.objects.create_superuser(
            employee_id='LEGACY-0003', password='oldpass123', name='Old Admin')
        for user in (self.worker, self.manager, self.admin):
            Token.objects.create(user=user)

    def add_correction_history(self):
        reason = OvershootReasons.objects.create(
            category='Mechanical', reason='Die change took longer')
        summary = ChangeoverSummary.objects.create(
            batch=1, setup_time_actual=40.0, standard_time=10)
        return ChangeoverCorrectionRequest.objects.create(
            changeover=summary, requested_by=self.worker, new_overshoot=reason)


class PurgeModeTests(RetireLegacyUsersBase):
    """No correction requests -> delete everything."""

    def test_reports_purge_mode(self):
        self.assertIn('Mode: PURGE', run('--dry-run'))

    def test_all_users_are_deleted(self):
        run('--no-input')
        self.assertEqual(User.objects.count(), 0)

    def test_all_tokens_are_revoked(self):
        run('--no-input')
        self.assertEqual(Token.objects.count(), 0)

    def test_warns_that_admin_access_will_be_lost(self):
        self.assertIn('/admin/ will be unreachable', run('--dry-run'))

    def test_keep_superusers_retires_them_instead(self):
        run('--no-input', '--keep-superusers')

        self.assertEqual(User.objects.count(), 1)
        survivor = User.objects.get()
        self.assertTrue(survivor.is_superuser)
        self.assertFalse(survivor.is_active)
        self.assertFalse(survivor.has_usable_password())


class RetireModeTests(RetireLegacyUsersBase):
    """Correction requests exist -> keep the rows, disable the accounts."""

    def setUp(self):
        super().setUp()
        self.correction = self.add_correction_history()

    def test_reports_retire_mode(self):
        self.assertIn('Mode: RETIRE', run('--dry-run'))

    def test_no_user_is_deleted(self):
        run('--no-input')
        self.assertEqual(User.objects.count(), 3)

    def test_correction_history_survives(self):
        run('--no-input')
        self.correction.refresh_from_db()
        self.assertEqual(self.correction.requested_by, self.worker)

    def test_every_account_is_deactivated(self):
        run('--no-input')
        self.assertEqual(User.objects.filter(is_active=True).count(), 0)

    def test_every_password_is_made_unusable(self):
        # Belt and braces: if someone later flips is_active back on, the
        # account still cannot be signed into.
        run('--no-input')
        for user in User.objects.all():
            with self.subTest(user=user.employee_id):
                self.assertFalse(user.has_usable_password())

    def test_old_credentials_no_longer_authenticate(self):
        run('--no-input')
        from django.contrib.auth import authenticate
        self.assertIsNone(
            authenticate(employee_id='LEGACY-0002', password='oldpass123'))

    def test_tokens_are_revoked(self):
        run('--no-input')
        self.assertEqual(Token.objects.count(), 0)

    def test_explains_why_the_rows_are_kept(self):
        # They stay only so correction-request history resolves to a name.
        self.assertIn('LEGACY-', run('--dry-run'))


class SafetyTests(RetireLegacyUsersBase):
    """Nothing changes without an explicit go-ahead."""

    def test_dry_run_changes_nothing(self):
        run('--dry-run')
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(Token.objects.count(), 3)
        self.assertIn('DRY RUN', run('--dry-run'))

    def test_declining_the_prompt_aborts(self):
        import builtins
        original = builtins.input
        builtins.input = lambda *a: 'no'
        try:
            with self.assertRaises(CommandError):
                run()
        finally:
            builtins.input = original
        self.assertEqual(User.objects.count(), 3)

    def test_confirming_the_prompt_proceeds(self):
        import builtins
        original = builtins.input
        builtins.input = lambda *a: 'yes'
        try:
            run()
        finally:
            builtins.input = original
        self.assertEqual(User.objects.count(), 0)

    def test_empty_user_table_is_a_no_op(self):
        Token.objects.all().delete()
        User.objects.all().delete()
        self.assertIn('Nothing to do', run('--dry-run'))
