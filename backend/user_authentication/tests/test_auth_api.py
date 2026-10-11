"""Sign-in and account claiming over the API.

There is no public signup. Accounts come from the employee roster and are
activated by the employee choosing their own password.
"""

from django.test import override_settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from user_authentication.models import User

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

GOOD_PASSWORD = 'Plant2026!x'


@fast_passwords
class ClaimAccountTests(APITestCase):

    URL = '/api/auth/claim/'

    def setUp(self):
        self.client = APIClient()
        self.pending = User.objects.create_roster_user(
            employee_id='10433', name='S Raman', role=User.Role.SHIFT_INCHARGE)

    def claim(self, employee_id='10433', password=GOOD_PASSWORD, confirm=None):
        return self.client.post(self.URL, {
            'employee_id': employee_id,
            'password': password,
            'confirm_password': confirm if confirm is not None else password,
        }, format='json')

    def test_claiming_a_pending_account_succeeds(self):
        response = self.claim()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_claiming_returns_a_token_and_the_user(self):
        response = self.claim()
        self.assertIn('token', response.data)
        self.assertEqual(response.data['user']['employee_id'], '10433')
        self.assertEqual(response.data['user']['role'], 'shift_incharge')

    def test_the_account_becomes_active(self):
        self.claim()
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, User.Status.ACTIVE)
        self.assertTrue(self.pending.is_active)

    def test_mismatched_passwords_are_rejected(self):
        response = self.claim(confirm='Different2026!x')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, User.Status.PENDING)

    def test_a_short_password_is_rejected(self):
        self.assertEqual(self.claim(password='short').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_a_common_password_is_rejected(self):
        # Django's validators apply, so the roster cannot be activated with
        # 'password1' and friends.
        self.assertEqual(self.claim(password='password123').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_an_already_claimed_account_cannot_be_reclaimed(self):
        self.claim()
        response = self.claim(password='Second2026!x')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reclaiming_does_not_change_the_password(self):
        self.claim()
        self.claim(password='Second2026!x')
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.check_password(GOOD_PASSWORD))

    def test_an_unknown_employee_id_is_refused(self):
        self.assertEqual(self.claim(employee_id='99999').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_id_and_a_claimed_id_give_the_same_message(self):
        # Otherwise this endpoint would reveal which employee IDs exist.
        self.claim()
        claimed = self.claim(password='Second2026!x')
        unknown = self.claim(employee_id='99999')
        self.assertEqual(str(claimed.data), str(unknown.data))

    def test_a_retired_account_cannot_be_claimed(self):
        self.pending.retire()
        self.assertEqual(self.claim().status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_id_is_normalised(self):
        response = self.claim(employee_id=' 10433 ')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_claiming_cannot_create_an_account(self):
        self.claim(employee_id='88888')
        self.assertFalse(User.objects.filter(employee_id='88888').exists())


@fast_passwords
class LoginTests(APITestCase):

    URL = '/api/auth/login/'

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            employee_id='10433', password=GOOD_PASSWORD, name='S Raman',
            role=User.Role.SHIFT_INCHARGE)

    def login(self, employee_id='10433', password=GOOD_PASSWORD):
        return self.client.post(
            self.URL, {'employee_id': employee_id, 'password': password},
            format='json')

    def test_correct_credentials_return_a_token(self):
        response = self.login()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)

    def test_the_response_carries_the_role(self):
        self.assertEqual(self.login().data['user']['role'], 'shift_incharge')

    def test_the_response_carries_no_password_field(self):
        self.assertNotIn('password', str(self.login().data))

    def test_employee_id_is_normalised(self):
        self.assertEqual(self.login(employee_id=' 10433 ').status_code,
                         status.HTTP_200_OK)

    def test_a_wrong_password_is_rejected(self):
        self.assertEqual(self.login(password='wrong-password').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_employee_id_is_rejected(self):
        self.assertEqual(self.login(employee_id='99999').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_an_unclaimed_account_gets_a_helpful_message(self):
        User.objects.create_roster_user(employee_id='10434', name='M Devi')
        response = self.login(employee_id='10434')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not been set up', str(response.data))

    def test_a_retired_account_is_told_it_is_inactive(self):
        self.user.retire()
        response = self.login()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('no longer active', str(response.data))

    def test_a_wrong_password_does_not_reveal_whether_the_id_exists(self):
        unknown = self.login(employee_id='99999', password='wrong-password')
        known = self.login(password='wrong-password')
        self.assertEqual(str(unknown.data), str(known.data))

    def test_logging_in_twice_reuses_the_token(self):
        first = self.login().data['token']
        second = self.login().data['token']
        self.assertEqual(first, second)
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)


@fast_passwords
class CurrentUserTests(APITestCase):

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get('/api/auth/me/').status_code,
                         status.HTTP_401_UNAUTHORIZED)

    def test_returns_the_signed_in_user(self):
        user = User.objects.create_user(
            employee_id='10001', password=GOOD_PASSWORD, name='P Mehta',
            role=User.Role.MANAGER)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get('/api/auth/me/')

        self.assertEqual(response.data['employee_id'], '10001')
        self.assertEqual(response.data['name'], 'P Mehta')
        self.assertEqual(response.data['role'], 'manager')
        self.assertEqual(response.data['role_display'], 'Manager')


@fast_passwords
class LastLoginTests(APITestCase):
    """last_login must actually be recorded.

    DRF token authentication never updates it, so unless the login view does
    it explicitly the field stays null for every account forever. The user
    list displays it, and account removal relies on it to tell a genuinely
    unused account from one whose password was merely reset.
    """

    def test_login_records_last_login(self):
        user = User.objects.create_user(
            employee_id='10433', password=GOOD_PASSWORD, name='S Raman')
        self.assertIsNone(user.last_login)

        APIClient().post('/api/auth/login/', {
            'employee_id': '10433', 'password': GOOD_PASSWORD}, format='json')

        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)

    def test_claiming_records_last_login(self):
        user = User.objects.create_roster_user(
            employee_id='10434', name='M Devi')

        APIClient().post('/api/auth/claim/', {
            'employee_id': '10434', 'password': GOOD_PASSWORD,
            'confirm_password': GOOD_PASSWORD}, format='json')

        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)

    def test_a_reset_does_not_clear_last_login(self):
        # This is what keeps a reset account distinguishable from a new one.
        user = User.objects.create_user(
            employee_id='10435', password=GOOD_PASSWORD, name='R Nair')
        APIClient().post('/api/auth/login/', {
            'employee_id': '10435', 'password': GOOD_PASSWORD}, format='json')
        user.refresh_from_db()
        signed_in_at = user.last_login

        admin = User.objects.create_superuser(
            employee_id='ADMINX', password='x', name='Admin')
        client = APIClient()
        client.force_authenticate(user=admin)
        client.post('/api/auth/users/10435/reset-password/')

        user.refresh_from_db()
        self.assertEqual(user.last_login, signed_in_at)
