"""Signing in puts an operator on the line; signing out takes them off.

The duty record is the sign-in rather than a separate screen, so these
tests cover the seam: what the login response tells the browser about its
own shift, and whether the session is closed when it should be.
"""

import datetime

from django.test import TestCase
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from data_processor.models import DutySession
from user_authentication.models import User

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

PASSWORD = 'line-pass-99'


def make_user(employee_id, role=User.Role.OPERATOR):
    return User.objects.create_user(
        employee_id=employee_id, password=PASSWORD,
        name=f'Person {employee_id}', role=role)


@fast_passwords
class LoginOpensDutyTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def login(self, employee_id, password=PASSWORD):
        return self.client.post(
            '/api/auth/login/',
            {'employee_id': employee_id, 'password': password}, format='json')

    def test_an_operator_signing_in_goes_on_duty(self):
        make_user('OP1')

        response = self.login('OP1')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DutySession.objects.count(), 1)

    def test_the_response_says_when_the_shift_ends(self):
        # This is the contract the browser counts down to.
        make_user('OP1')

        duty = self.login('OP1').data['duty']

        self.assertIsNotNone(duty)
        self.assertIn('shift_ends_at', duty)
        self.assertIn('seconds_remaining', duty)
        self.assertGreater(duty['seconds_remaining'], 0)

    def test_the_shift_end_is_the_next_boundary(self):
        from data_processor.shifts import shift_end_after
        make_user('OP1')

        duty = self.login('OP1').data['duty']
        session = DutySession.objects.get()

        self.assertEqual(
            datetime.datetime.fromisoformat(duty['shift_ends_at']),
            shift_end_after(session.started_at))

    def test_a_manager_is_not_put_on_duty(self):
        # Managers are never signed out at a boundary, so they are told
        # there is no shift attached to this session.
        make_user('MGR1', role=User.Role.MANAGER)

        response = self.login('MGR1')

        self.assertIsNone(response.data['duty'])
        self.assertFalse(DutySession.objects.exists())

    def test_a_shift_incharge_is_not_put_on_duty(self):
        make_user('SI1', role=User.Role.SHIFT_INCHARGE)

        response = self.login('SI1')

        self.assertIsNone(response.data['duty'])
        self.assertFalse(DutySession.objects.exists())

    def test_a_failed_sign_in_puts_nobody_on_duty(self):
        make_user('OP1')

        response = self.login('OP1', password='wrong')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DutySession.objects.exists())

    def test_signing_in_twice_leaves_one_open_session(self):
        make_user('OP1')

        self.login('OP1')
        self.login('OP1')

        self.assertEqual(
            DutySession.objects.filter(ended_at__isnull=True).count(), 1)
        self.assertEqual(DutySession.objects.count(), 2)


@fast_passwords
class ClaimOpensDutyTests(TestCase):

    def test_claiming_an_account_also_starts_the_shift(self):
        # A new operator's first sign-in is the claim, so it has to put
        # them on duty too -- otherwise their first shift is unattributed.
        User.objects.create_roster_user(employee_id='OP9', name='New Starter')
        client = APIClient()

        response = client.post('/api/auth/claim/', {
            'employee_id': 'OP9',
            'password': 'first-password-1',
            'confirm_password': 'first-password-1',
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['duty'])
        self.assertEqual(DutySession.objects.count(), 1)


@fast_passwords
class CurrentUserDutyTests(TestCase):

    def test_me_reports_the_duty_session(self):
        operator = make_user('OP1')
        client = APIClient()
        client.force_authenticate(operator)
        from data_processor.setup_capture import open_duty_session
        open_duty_session(operator)

        response = client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['duty'])
        self.assertEqual(response.data['duty']['shift'],
                         DutySession.objects.get().shift)

    def test_me_reports_no_duty_for_an_operator_not_signed_in(self):
        operator = make_user('OP1')
        client = APIClient()
        client.force_authenticate(operator)

        self.assertIsNone(client.get('/api/auth/me/').data['duty'])

    def test_me_reports_no_duty_for_a_manager(self):
        manager = make_user('MGR1', role=User.Role.MANAGER)
        client = APIClient()
        client.force_authenticate(manager)

        self.assertIsNone(client.get('/api/auth/me/').data['duty'])


@fast_passwords
class LogoutTests(TestCase):
    """There was no sign-out endpoint at all before this."""

    def setUp(self):
        self.operator = make_user('OP1')
        self.client = APIClient()

    def sign_in(self):
        response = self.client.post(
            '/api/auth/login/',
            {'employee_id': 'OP1', 'password': PASSWORD}, format='json')
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        return response.data['token']

    def test_signing_out_closes_the_duty_session(self):
        self.sign_in()

        response = self.client.post('/api/auth/logout/')

        self.assertEqual(response.status_code, 200)
        session = DutySession.objects.get()
        self.assertIsNotNone(session.ended_at)
        self.assertEqual(session.end_reason, DutySession.EndReason.SIGNED_OUT)

    def test_signing_out_destroys_the_token(self):
        # The token used to live forever; the client just forgot it.
        token = self.sign_in()

        self.client.post('/api/auth/logout/')

        self.assertFalse(Token.objects.filter(key=token).exists())

    def test_the_old_token_stops_working(self):
        self.sign_in()
        self.client.post('/api/auth/logout/')

        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)

    def test_a_manager_can_sign_out_without_a_duty_session(self):
        manager = make_user('MGR1', role=User.Role.MANAGER)
        client = APIClient()
        client.force_authenticate(manager)

        response = client.post('/api/auth/logout/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['duty_sessions_closed'], 0)

    def test_signing_out_without_a_token_is_refused(self):
        self.assertEqual(
            APIClient().post('/api/auth/logout/').status_code, 401)
