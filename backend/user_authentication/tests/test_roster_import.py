"""import_employee_roster: turning the company list into accounts."""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from user_authentication.models import User

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

HEADER = 'employee_id,name,role\n'


def write_csv(body, header=HEADER):
    handle = tempfile.NamedTemporaryFile(
        'w', suffix='.csv', delete=False, encoding='utf-8')
    handle.write(header + body)
    handle.close()
    return handle.name


def run(path, *args):
    out = StringIO()
    call_command('import_employee_roster', path, *args, stdout=out, stderr=out)
    return out.getvalue()


@fast_passwords
class RosterImportTests(TestCase):

    def test_creates_one_pending_account_per_row(self):
        path = write_csv('10001,P Mehta,manager\n10432,A Kumar,operator\n')

        run(path)

        self.assertEqual(User.objects.count(), 2)
        self.assertTrue(all(u.status == User.Status.PENDING
                            for u in User.objects.all()))

    def test_no_account_has_a_usable_password(self):
        # The import never sets a password -- employees choose their own.
        path = write_csv('10001,P Mehta,manager\n')
        run(path)
        self.assertFalse(User.objects.get().has_usable_password())

    def test_role_spellings_are_accepted(self):
        path = write_csv(
            '1,A,manager\n2,B,Shift Incharge\n3,C,shift-incharge\n'
            '4,D,SHIFT_INCHARGE\n5,E,OPERATOR\n')

        run(path)

        self.assertEqual(
            User.objects.get(employee_id='2').role, User.Role.SHIFT_INCHARGE)
        self.assertEqual(
            User.objects.get(employee_id='3').role, User.Role.SHIFT_INCHARGE)
        self.assertEqual(
            User.objects.get(employee_id='4').role, User.Role.SHIFT_INCHARGE)
        self.assertEqual(
            User.objects.get(employee_id='5').role, User.Role.OPERATOR)

    def test_employee_ids_are_normalised(self):
        path = write_csv(' 10432 ,A Kumar,operator\n')
        run(path)
        self.assertTrue(User.objects.filter(employee_id='10432').exists())

    def test_dry_run_writes_nothing(self):
        path = write_csv('10001,P Mehta,manager\n')
        output = run(path, '--dry-run')
        self.assertEqual(User.objects.count(), 0)
        self.assertIn('DRY RUN', output)

    def test_rerunning_updates_rather_than_duplicating(self):
        path = write_csv('10001,P Mehta,manager\n')
        run(path)
        run(write_csv('10001,P Mehta Jr,manager\n'))

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().name, 'P Mehta Jr')

    def test_a_role_change_flows_through(self):
        run(write_csv('10433,S Raman,operator\n'))
        run(write_csv('10433,S Raman,shift_incharge\n'))
        self.assertEqual(
            User.objects.get(employee_id='10433').role, User.Role.SHIFT_INCHARGE)

    def test_a_claimed_account_keeps_its_password_on_reimport(self):
        # An HR refresh must not lock anyone out.
        run(write_csv('10433,S Raman,shift_incharge\n'))
        user = User.objects.get(employee_id='10433')
        user.claim('Plant2026!x')

        run(write_csv('10433,S Raman,shift_incharge\n'))

        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.check_password('Plant2026!x'))


class RosterValidationTests(TestCase):
    """A bad roster imports nothing at all, rather than half of it."""

    def test_missing_file(self):
        with self.assertRaises(CommandError):
            run('/nonexistent/roster.csv')

    def test_missing_columns(self):
        path = write_csv('10001,P Mehta\n', header='employee_id,name\n')
        with self.assertRaises(CommandError):
            run(path)

    def test_unknown_role_aborts_the_whole_import(self):
        path = write_csv('10001,P Mehta,manager\n10432,A Kumar,supervisor\n')
        with self.assertRaises(CommandError):
            run(path)
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_employee_id_aborts(self):
        path = write_csv('10001,P Mehta,manager\n10001,Someone Else,operator\n')
        with self.assertRaises(CommandError):
            run(path)

    def test_blank_name_aborts(self):
        with self.assertRaises(CommandError):
            run(write_csv('10001,,manager\n'))

    def test_blank_employee_id_aborts(self):
        with self.assertRaises(CommandError):
            run(write_csv(',P Mehta,manager\n'))


@fast_passwords
class AdminFlagTests(TestCase):
    """--admin is how an administrator is made; there is no admin role."""

    def test_grants_the_django_flags(self):
        path = write_csv('10001,P Mehta,manager\n10432,A Kumar,operator\n')

        run(path, '--admin', '10001')

        admin = User.objects.get(employee_id='10001')
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertEqual(admin.role, User.Role.MANAGER)

    def test_nobody_else_gets_them(self):
        path = write_csv('10001,P Mehta,manager\n10432,A Kumar,operator\n')
        run(path, '--admin', '10001')
        other = User.objects.get(employee_id='10432')
        self.assertFalse(other.is_staff)
        self.assertFalse(other.is_superuser)

    def test_a_non_manager_cannot_be_made_admin(self):
        path = write_csv('10432,A Kumar,operator\n')
        with self.assertRaises(CommandError):
            run(path, '--admin', '10432')

    def test_an_id_not_in_the_roster_is_rejected(self):
        path = write_csv('10001,P Mehta,manager\n')
        with self.assertRaises(CommandError):
            run(path, '--admin', '99999')
