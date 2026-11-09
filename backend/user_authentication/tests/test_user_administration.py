"""Adding people, changing their role, and resetting a forgotten password.

The ladder:

    MANAGER  adds and edits operators and shift incharges
    ADMIN    the above, plus creating managers and resetting passwords

Nobody ever sets somebody else's password. A new account is PENDING with no
usable password, and the person chooses their own at first sign-in. A
password reset puts the account back into exactly that state.
"""

import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from data_processor.models import (
    ChangeoverCorrectionRequest,
    ChangeoverSummary,
    OvershootReasons,
)
from user_authentication.models import User

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

USERS_URL = '/api/auth/users/'


@fast_passwords
class SeedAdminCommandTests(APITestCase):
    """The one bootstrap account.

    The password has no default: this repository is public, and a
    built-in administrator password is one anybody can read. It comes
    from --password or the ADMIN_SEED_PASSWORD environment variable.
    """

    SEED_PASSWORD = 'bootstrap-pass-9'

    def seed(self, *args, password=None):
        out = StringIO()
        if password is None:
            password = self.SEED_PASSWORD
        call_command('seed_admin_user', '--password', password,
                     *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_creates_the_bootstrap_administrator(self):
        self.seed()

        admin = User.objects.get(employee_id='ADMIN_JK')
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertEqual(admin.role, User.Role.MANAGER)
        self.assertEqual(admin.status, User.Status.ACTIVE)

    def test_the_seeded_admin_can_sign_in(self):
        self.seed()
        response = APIClient().post(
            '/api/auth/login/',
            {'employee_id': 'admin_jk', 'password': self.SEED_PASSWORD},
            format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_it_refuses_to_invent_a_password(self):
        # No default, on purpose. A built-in administrator password on a
        # public repository is a published credential.
        out = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command('seed_admin_user', stdout=out, stderr=out)
        self.assertIn('No password given', str(caught.exception))
        self.assertFalse(User.objects.exists())

    def test_it_reads_the_password_from_the_environment(self):
        with mock.patch.dict(
                os.environ, {'ADMIN_SEED_PASSWORD': 'from-the-env-1'}):
            out = StringIO()
            call_command('seed_admin_user', stdout=out, stderr=out)

        admin = User.objects.get(employee_id='ADMIN_JK')
        self.assertTrue(admin.check_password('from-the-env-1'))

    def test_rerunning_is_a_no_op(self):
        self.seed()
        output = self.seed()
        self.assertIn('already exists', output)
        self.assertEqual(User.objects.count(), 1)

    def test_reset_password_flag_restores_access(self):
        self.seed()
        admin = User.objects.get(employee_id='ADMIN_JK')
        admin.set_password('something-else')
        admin.save()

        self.seed('--reset-password')

        admin.refresh_from_db()
        self.assertTrue(admin.check_password(self.SEED_PASSWORD))


@fast_passwords
class UserAdministrationBase(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_superuser(
            employee_id='ADMIN_JK', password='admin-pass-77',
            name='Administrator')
        self.manager = User.objects.create_user(
            employee_id='20003', password='x', name='P Mehta',
            role=User.Role.MANAGER)
        self.shift_incharge = User.objects.create_user(
            employee_id='20002', password='x', name='S Raman',
            role=User.Role.SHIFT_INCHARGE)
        self.operator = User.objects.create_user(
            employee_id='20001', password='x', name='A Kumar',
            role=User.Role.OPERATOR)
        self.client = APIClient()

    def as_(self, user):
        self.client.force_authenticate(user=user)
        return self.client


class ListUsersTests(UserAdministrationBase):

    def test_a_manager_can_list_everyone(self):
        response = self.as_(self.manager).get(USERS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 4)

    def test_the_listing_exposes_no_password_data(self):
        response = self.as_(self.manager).get(USERS_URL)
        self.assertNotIn('password', str(response.data))

    def test_a_shift_incharge_cannot_list(self):
        self.assertEqual(self.as_(self.shift_incharge).get(USERS_URL).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_an_operator_cannot_list(self):
        self.assertEqual(self.as_(self.operator).get(USERS_URL).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_list(self):
        self.assertEqual(APIClient().get(USERS_URL).status_code,
                         status.HTTP_401_UNAUTHORIZED)


class CreateUserTests(UserAdministrationBase):

    def payload(self, employee_id='30001', role=User.Role.OPERATOR):
        return {'employee_id': employee_id, 'name': 'New Person', 'role': role}

    def test_a_manager_can_add_an_operator(self):
        response = self.as_(self.manager).post(
            USERS_URL, self.payload(role=User.Role.OPERATOR), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_manager_can_add_a_shift_incharge(self):
        response = self.as_(self.manager).post(
            USERS_URL, self.payload(role=User.Role.SHIFT_INCHARGE), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_manager_cannot_add_a_manager(self):
        response = self.as_(self.manager).post(
            USERS_URL, self.payload(role=User.Role.MANAGER), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('administrator', str(response.data).lower())

    def test_the_admin_can_add_a_manager(self):
        response = self.as_(self.admin).post(
            USERS_URL, self.payload(role=User.Role.MANAGER), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_new_account_starts_pending_with_no_password(self):
        self.as_(self.manager).post(USERS_URL, self.payload(), format='json')

        created = User.objects.get(employee_id='30001')
        self.assertEqual(created.status, User.Status.PENDING)
        self.assertFalse(created.is_active)
        self.assertFalse(created.has_usable_password())

    def test_the_new_person_can_claim_and_sign_in(self):
        self.as_(self.manager).post(USERS_URL, self.payload(), format='json')

        client = APIClient()
        claim = client.post('/api/auth/claim/', {
            'employee_id': '30001', 'password': 'Plant2026!x',
            'confirm_password': 'Plant2026!x'}, format='json')
        self.assertEqual(claim.status_code, status.HTTP_201_CREATED)

        login = client.post('/api/auth/login/', {
            'employee_id': '30001', 'password': 'Plant2026!x'}, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_a_duplicate_employee_id_is_rejected(self):
        response = self.as_(self.manager).post(
            USERS_URL, self.payload(employee_id='20001'), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_duplicate_is_caught_across_spellings(self):
        response = self.as_(self.manager).post(
            USERS_URL, self.payload(employee_id=' 20001 '), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_id_is_normalised(self):
        self.as_(self.manager).post(
            USERS_URL, self.payload(employee_id=' jk 55 '), format='json')
        self.assertTrue(User.objects.filter(employee_id='JK55').exists())

    def test_a_blank_employee_id_is_rejected(self):
        response = self.as_(self.manager).post(
            USERS_URL, self.payload(employee_id='   '), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_shift_incharge_cannot_add_anyone(self):
        response = self.as_(self.shift_incharge).post(
            USERS_URL, self.payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_password_can_be_set_through_this_endpoint(self):
        self.as_(self.manager).post(
            USERS_URL, {**self.payload(), 'password': 'SneakyPass1!'},
            format='json')
        created = User.objects.get(employee_id='30001')
        self.assertFalse(created.has_usable_password())


class UpdateUserTests(UserAdministrationBase):

    def url(self, user):
        return f'{USERS_URL}{user.employee_id}/'

    def test_a_manager_can_rename_an_operator(self):
        response = self.as_(self.manager).patch(
            self.url(self.operator), {'name': 'A Kumar Jr'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.name, 'A Kumar Jr')

    def test_a_manager_can_promote_an_operator_to_shift_incharge(self):
        response = self.as_(self.manager).patch(
            self.url(self.operator), {'role': User.Role.SHIFT_INCHARGE},
            format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_a_manager_cannot_promote_anyone_to_manager(self):
        response = self.as_(self.manager).patch(
            self.url(self.operator), {'role': User.Role.MANAGER}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_admin_can_promote_to_manager(self):
        response = self.as_(self.admin).patch(
            self.url(self.operator), {'role': User.Role.MANAGER}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_a_manager_cannot_demote_another_manager(self):
        response = self.as_(self.manager).patch(
            self.url(self.manager), {'role': User.Role.OPERATOR}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_updating_does_not_disturb_the_password(self):
        self.as_(self.manager).patch(
            self.url(self.operator), {'name': 'Renamed'}, format='json')
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.check_password('x'))


class ResetPasswordTests(UserAdministrationBase):
    """Forgotten password: send the account back to PENDING."""

    def url(self, user):
        return f'{USERS_URL}{user.employee_id}/reset-password/'

    def test_the_admin_can_reset_an_operator(self):
        response = self.as_(self.admin).post(self.url(self.operator))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_the_account_returns_to_pending(self):
        self.as_(self.admin).post(self.url(self.operator))

        self.operator.refresh_from_db()
        self.assertEqual(self.operator.status, User.Status.PENDING)
        self.assertFalse(self.operator.is_active)
        self.assertFalse(self.operator.has_usable_password())

    def test_the_old_password_stops_working(self):
        self.as_(self.admin).post(self.url(self.operator))
        response = APIClient().post(
            '/api/auth/login/',
            {'employee_id': '20001', 'password': 'x'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_existing_sessions_are_revoked(self):
        Token.objects.create(user=self.operator)
        self.as_(self.admin).post(self.url(self.operator))
        self.assertFalse(Token.objects.filter(user=self.operator).exists())

    def test_the_person_can_set_a_new_password(self):
        self.as_(self.admin).post(self.url(self.operator))

        client = APIClient()
        claim = client.post('/api/auth/claim/', {
            'employee_id': '20001', 'password': 'Brandnew2026!x',
            'confirm_password': 'Brandnew2026!x'}, format='json')
        self.assertEqual(claim.status_code, status.HTTP_201_CREATED)

        login = client.post('/api/auth/login/', {
            'employee_id': '20001', 'password': 'Brandnew2026!x'}, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_a_manager_cannot_reset_anyone(self):
        response = self.as_(self.manager).post(self.url(self.operator))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_operator_cannot_reset_anyone(self):
        response = self.as_(self.operator).post(self.url(self.shift_incharge))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unknown_employee_id_returns_404(self):
        response = self.as_(self.admin).post(f'{USERS_URL}99999/reset-password/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_administrator_cannot_be_reset_here(self):
        # Avoids one admin locking another out of the only bootstrap account.
        other = User.objects.create_superuser(
            employee_id='ADMIN2', password='x', name='Other Admin')
        response = self.as_(self.admin).post(self.url(other))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RemoveUserTests(UserAdministrationBase):
    """Removing somebody NEVER deletes the row.

    Two reasons. ChangeoverCorrectionRequest.requested_by is a CASCADE
    foreign key, so deleting a person who filed a correction would take that
    record with them. And user records are needed by upcoming features, so
    every account that has ever existed stays queryable.
    """

    def url(self, user):
        return f'{USERS_URL}{user.employee_id}/'

    def _give_history(self, user):
        reason = OvershootReasons.objects.create(
            category='Mechanical', reason='Die change took longer')
        summary = ChangeoverSummary.objects.create(
            batch=99, setup_time_actual=40.0, standard_time=10)
        return ChangeoverCorrectionRequest.objects.create(
            changeover=summary, requested_by=user, new_overshoot=reason)

    # ------------------------------------------------- retire, not delete

    def test_a_claimed_account_is_retired_not_deleted(self):
        response = self.as_(self.admin).delete(self.url(self.operator))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['action'], 'retired')
        self.assertTrue(User.objects.filter(employee_id='20001').exists())

    def test_a_retired_account_cannot_sign_in(self):
        self.as_(self.admin).delete(self.url(self.operator))

        self.operator.refresh_from_db()
        self.assertEqual(self.operator.status, User.Status.RETIRED)
        self.assertFalse(self.operator.is_active)
        self.assertFalse(self.operator.has_usable_password())

    def test_correction_history_survives_the_removal(self):
        correction = self._give_history(self.operator)

        self.as_(self.admin).delete(self.url(self.operator))

        correction.refresh_from_db()
        self.assertEqual(correction.requested_by, self.operator)

    def test_history_is_kept_even_for_a_pending_account(self):
        # Never signed in, but somebody filed on their behalf -- the row has
        # to stay or the correction goes with it.
        pending = User.objects.create_roster_user(
            employee_id='40001', name='Never Used')
        self._give_history(pending)

        response = self.as_(self.admin).delete(self.url(pending))

        self.assertEqual(response.data['action'], 'retired')
        self.assertTrue(User.objects.filter(employee_id='40001').exists())

    def test_tokens_are_revoked(self):
        Token.objects.create(user=self.operator)
        self.as_(self.admin).delete(self.url(self.operator))
        self.assertFalse(Token.objects.filter(user=self.operator).exists())

    # -------------------------------------------- nothing is ever deleted

    def test_even_a_never_used_account_is_only_retired(self):
        # A typo'd employee ID still leaves a row behind. That is the
        # deliberate trade: no record is ever destroyed.
        unused = User.objects.create_roster_user(
            employee_id='40002', name='Typo Account')

        response = self.as_(self.admin).delete(self.url(unused))

        self.assertEqual(response.data['action'], 'retired')
        self.assertTrue(User.objects.filter(employee_id='40002').exists())

    def test_no_removal_path_deletes_a_row(self):
        before = User.objects.count()

        for employee_id in ('20001', '20002'):
            self.as_(self.admin).delete(f'{USERS_URL}{employee_id}/')

        self.assertEqual(User.objects.count(), before)

    def test_a_removed_id_cannot_be_reissued(self):
        # The consequence of never deleting: employee_id is unique, and a
        # retired row still holds it. The same person is restored instead.
        self.as_(self.admin).delete(self.url(self.operator))

        response = self.as_(self.admin).post(
            USERS_URL,
            {'employee_id': '20001', 'name': 'Someone Else',
             'role': User.Role.OPERATOR},
            format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_retired_id_cannot_be_reissued(self):
        # The row still occupies the unique employee_id, by design.
        self.as_(self.admin).delete(self.url(self.operator))

        response = self.as_(self.admin).post(
            USERS_URL,
            {'employee_id': '20001', 'name': 'Someone Else',
             'role': User.Role.OPERATOR},
            format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --------------------------------------------------------- guardrails

    def test_a_manager_cannot_remove_anyone(self):
        response = self.as_(self.manager).delete(self.url(self.operator))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_operator_cannot_remove_anyone(self):
        response = self.as_(self.operator).delete(self.url(self.shift_incharge))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_admin_cannot_remove_themselves(self):
        response = self.as_(self.admin).delete(self.url(self.admin))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(employee_id='ADMIN_JK').exists())

    def test_another_administrator_cannot_be_removed(self):
        other = User.objects.create_superuser(
            employee_id='ADMIN2', password='x', name='Other Admin')
        response = self.as_(self.admin).delete(self.url(other))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RetiredAreHiddenTests(UserAdministrationBase):
    """A removed employee disappears from the app.

    The row stays in the database -- correction history points at it, and
    upcoming features need to resolve past users -- but nobody administering
    people should see somebody who has left.
    """

    def setUp(self):
        super().setUp()
        self.as_(self.admin).delete(f'{USERS_URL}{self.operator.employee_id}/')
        self.operator.refresh_from_db()

    def test_the_row_still_exists(self):
        self.assertTrue(User.objects.filter(employee_id='20001').exists())
        self.assertEqual(self.operator.status, User.Status.RETIRED)

    def test_they_are_absent_from_the_user_list(self):
        response = self.as_(self.manager).get(USERS_URL)
        ids = [u['employee_id'] for u in response.data]
        self.assertNotIn('20001', ids)

    def test_everybody_else_is_still_listed(self):
        response = self.as_(self.manager).get(USERS_URL)
        ids = [u['employee_id'] for u in response.data]
        self.assertIn('20002', ids)
        self.assertIn('20003', ids)

    def test_there_is_no_restore_endpoint(self):
        response = self.as_(self.admin).post(f'{USERS_URL}20001/restore/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_their_password_cannot_be_reset(self):
        # Resetting is for somebody who still works here.
        response = self.as_(self.admin).post(
            f'{USERS_URL}20001/reset-password/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('removed', str(response.data).lower())

    def test_they_cannot_sign_in(self):
        response = APIClient().post(
            '/api/auth/login/',
            {'employee_id': '20001', 'password': 'x'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_their_employee_id_cannot_be_reused(self):
        response = self.as_(self.admin).post(
            USERS_URL,
            {'employee_id': '20001', 'name': 'Someone New',
             'role': User.Role.OPERATOR},
            format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_reuse_error_explains_why(self):
        # The row is hidden, so "already exists" alone would send an admin
        # hunting for a user they cannot see.
        response = self.as_(self.admin).post(
            USERS_URL,
            {'employee_id': '20001', 'name': 'Someone New',
             'role': User.Role.OPERATOR},
            format='json')
        self.assertIn('removed', str(response.data).lower())


class StatusLabelTests(UserAdministrationBase):
    """The status a manager reads in the list."""

    def test_awaiting_password_reads_plainly(self):
        pending = User.objects.create_roster_user(
            employee_id='40100', name='New Joiner')
        self.assertEqual(pending.get_status_display(), 'Awaiting password')

    def test_a_reset_account_reads_the_same_as_a_new_one(self):
        # One status, deliberately: both mean "choose a password next time".
        self.as_(self.admin).post(
            f'{USERS_URL}{self.operator.employee_id}/reset-password/')
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.get_status_display(), 'Awaiting password')

    def test_removed_reads_as_removed(self):
        self.as_(self.admin).delete(f'{USERS_URL}{self.operator.employee_id}/')
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.get_status_display(), 'Removed')
