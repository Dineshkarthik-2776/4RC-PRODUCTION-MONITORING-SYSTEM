"""An administrator changing their own password.

Nobody can reset the administrator: there is no higher account, and the
reset endpoint refuses other administrators by design. So the administrator
changes their own password in place, proving they know the current one.
"""

from django.test import override_settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from user_authentication.models import User

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

VERIFY_URL = '/api/auth/me/verify-password/'
CHANGE_URL = '/api/auth/me/change-password/'
# A throwaway fixture password, not a real one. The seed command
# has no built-in default any more -- it refuses to invent one.
CURRENT = 'bootstrap-pass-9'
NEW = 'Plant2026!x'


@fast_passwords
class OwnPasswordBase(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_superuser(
            employee_id='ADMIN_JK', password=CURRENT, name='Administrator')
        self.manager = User.objects.create_user(
            employee_id='20003', password=CURRENT, name='P Mehta',
            role=User.Role.MANAGER)
        self.operator = User.objects.create_user(
            employee_id='20001', password=CURRENT, name='A Kumar',
            role=User.Role.OPERATOR)
        self.client = APIClient()

    def as_(self, user):
        self.client.force_authenticate(user=user)
        return self.client


class VerifyStepTests(OwnPasswordBase):
    """Step one: confirm the current password before asking for a new one."""

    def test_the_correct_password_verifies(self):
        response = self.as_(self.admin).post(
            VERIFY_URL, {'password': CURRENT}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['verified'])

    def test_a_wrong_password_is_refused(self):
        response = self.as_(self.admin).post(
            VERIFY_URL, {'password': 'not-my-password'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not your current password', str(response.data))

    def test_verifying_changes_nothing(self):
        self.as_(self.admin).post(VERIFY_URL, {'password': CURRENT},
                                  format='json')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(CURRENT))

    def test_a_manager_cannot_use_it(self):
        response = self.as_(self.manager).post(
            VERIFY_URL, {'password': CURRENT}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_use_it(self):
        response = APIClient().post(
            VERIFY_URL, {'password': CURRENT}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ChangeStepTests(OwnPasswordBase):
    """Step two: set the new password."""

    def change(self, user=None, current=CURRENT, new=NEW, confirm=None):
        return self.as_(user or self.admin).post(CHANGE_URL, {
            'current_password': current,
            'new_password': new,
            'confirm_password': confirm if confirm is not None else new,
        }, format='json')

    def test_the_password_changes(self):
        response = self.change()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(NEW))

    def test_the_old_password_stops_working(self):
        self.change()
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.check_password(CURRENT))

    def test_the_new_password_signs_in(self):
        self.change()
        response = APIClient().post(
            '/api/auth/login/',
            {'employee_id': 'ADMIN_JK', 'password': NEW}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_the_current_password_is_rechecked_here(self):
        # Passing the verify step is not a ticket: a client could post
        # straight to this endpoint, so it has to stand on its own.
        response = self.change(current='wrong-password')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(CURRENT))

    def test_mismatched_new_passwords_are_refused(self):
        response = self.change(confirm='Different2026!x')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reusing_the_current_password_is_refused(self):
        # Needs a current password that would otherwise pass validation,
        # so the "must be different" rule is what rejects it rather than the
        # length check, which a 7-character password trips first.
        self.admin.set_password('Compliant2026!x')
        self.admin.save()

        response = self.change(current='Compliant2026!x',
                               new='Compliant2026!x',
                               confirm='Compliant2026!x')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('different', str(response.data).lower())

    def test_a_weak_password_is_refused(self):
        response = self.change(new='password123', confirm='password123')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_short_password_is_refused(self):
        response = self.change(new='short', confirm='short')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_manager_cannot_use_it(self):
        response = self.change(user=self.manager)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_operator_cannot_use_it(self):
        response = self.change(user=self.operator)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_manager_password_is_untouched_by_an_admin_change(self):
        self.change()
        self.manager.refresh_from_db()
        self.assertTrue(self.manager.check_password(CURRENT))


class SeededAdminPasswordTests(OwnPasswordBase):
    """create_superuser does not run AUTH_PASSWORD_VALIDATORS.

    So whatever password seed_admin_user is given goes in unchecked,
    even one no employee would be allowed to choose. The command no
    longer carries a default of its own -- it refuses to invent one --
    but a deployer can still pass something weak, and this records that
    the gap is in create_superuser rather than in the command.
    """

    def test_a_weak_bootstrap_password_is_not_validated(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        # Seven characters: shorter than the 8 the validators demand.
        weak = 'seed123'
        with self.assertRaises(ValidationError):
            validate_password(weak, self.admin)

        # And yet create_superuser accepts it without complaint.
        bootstrapped = User.objects.create_superuser(
            employee_id='BOOT1', password=weak, name='Bootstrap')
        self.assertTrue(bootstrapped.check_password(weak))

    def test_a_deliberate_change_is_held_to_the_rules(self):
        # Whatever the account starts with, choosing a new password goes
        # through AUTH_PASSWORD_VALIDATORS.
        for weak in ('12345678', 'password', 'abc'):
            with self.subTest(password=weak):
                response = self.as_(self.admin).post(CHANGE_URL, {
                    'current_password': CURRENT, 'new_password': weak,
                    'confirm_password': weak}, format='json')
                self.assertEqual(response.status_code,
                                 status.HTTP_400_BAD_REQUEST)


class TokenRotationTests(OwnPasswordBase):
    """A session opened with the old password must not survive."""

    def test_the_old_token_is_revoked(self):
        old = Token.objects.create(user=self.admin)

        self.as_(self.admin).post(CHANGE_URL, {
            'current_password': CURRENT, 'new_password': NEW,
            'confirm_password': NEW}, format='json')

        self.assertFalse(Token.objects.filter(key=old.key).exists())

    def test_a_fresh_token_is_returned(self):
        response = self.as_(self.admin).post(CHANGE_URL, {
            'current_password': CURRENT, 'new_password': NEW,
            'confirm_password': NEW}, format='json')

        self.assertIn('token', response.data)
        self.assertTrue(
            Token.objects.filter(key=response.data['token'],
                                 user=self.admin).exists())

    def test_the_returned_token_works(self):
        response = self.as_(self.admin).post(CHANGE_URL, {
            'current_password': CURRENT, 'new_password': NEW,
            'confirm_password': NEW}, format='json')

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {response.data["token"]}')
        self.assertEqual(client.get('/api/auth/me/').status_code,
                         status.HTTP_200_OK)
