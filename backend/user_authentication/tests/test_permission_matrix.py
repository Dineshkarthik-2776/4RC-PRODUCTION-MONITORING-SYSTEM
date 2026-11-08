"""Every endpoint against every role, in one parametrised sweep.

The matrix the plant agreed on:

    OPERATOR        read-only: dashboard, summaries, charts, filters, and the
                    Excel / Kepware / PDF downloads.
    SHIFT_INCHARGE  the above, plus recording overshoot reasons.
    MANAGER         the above, plus the masters, correction approval and the
                    5-hour override.

Anonymous callers get nothing. A test here failing means the privilege
boundary moved, which is exactly the kind of change that should never happen
by accident.
"""

import datetime

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from data_processor.models import (
    ChangeoverSummary,
    OvershootReasons,
    RecipeMaster,
    StandardTimeMaster,
)
from data_processor.tests.base import UnmanagedTableMixin
from user_authentication.models import User

fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

ALLOWED = 'allowed'
FORBIDDEN = 'forbidden'


@fast_passwords
class PermissionMatrixTests(UnmanagedTableMixin, APITestCase):

    def setUp(self):
        self.operator = User.objects.create_user(
            employee_id='20001', password='x', name='A Kumar',
            role=User.Role.OPERATOR)
        self.shift_incharge = User.objects.create_user(
            employee_id='20002', password='x', name='S Raman',
            role=User.Role.SHIFT_INCHARGE)
        self.manager = User.objects.create_user(
            employee_id='20003', password='x', name='P Mehta',
            role=User.Role.MANAGER)

        self.recipe = RecipeMaster.objects.create(
            recipe_code='BRTTL', target_speed=40.0, recipe_type='fabric')
        self.standard = StandardTimeMaster.objects.create(
            changeover_key='Fabric to Fabric', standard_time=10.0, is_custom=True)
        self.reason = OvershootReasons.objects.create(
            category='Mechanical', reason='Die change took longer')
        self.summary = ChangeoverSummary.objects.create(
            batch=1,
            recipe_change_time=datetime.datetime.now() - datetime.timedelta(hours=1),
            setup_complete_timestamp=datetime.datetime.now() - datetime.timedelta(hours=1),
            current_recipe='BRTTL', previous_recipe='BRTTL',
            change_over_type='Fabric to Fabric',
            setup_time_actual=40.0, standard_time=10)

    def check(self, user, method, url, expectation, payload=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        response = getattr(client, method)(url, payload or {}, format='json')

        if user is None:
            self.assertEqual(
                response.status_code, status.HTTP_401_UNAUTHORIZED,
                f'anonymous {method.upper()} {url} should be rejected')
            return

        if expectation == FORBIDDEN:
            self.assertEqual(
                response.status_code, status.HTTP_403_FORBIDDEN,
                f'{user.role} {method.upper()} {url} should be forbidden, '
                f'got {response.status_code}')
        else:
            self.assertNotIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                f'{user.role} {method.upper()} {url} should be allowed, '
                f'got {response.status_code}')

    def sweep(self, method, url, operator, shift_incharge, manager, payload=None):
        for user, expectation in (
            (None, FORBIDDEN),
            (self.operator, operator),
            (self.shift_incharge, shift_incharge),
            (self.manager, manager),
        ):
            label = user.role if user else 'anonymous'
            with self.subTest(role=label, url=url, method=method):
                self.check(user, method, url, expectation, payload)

    # ------------------------------------------------- everyone can read

    def test_dashboard_is_open_to_all_roles(self):
        self.sweep('get',
                   '/api/changeover-stats/?from_date=2026-04-01&to_date=2026-04-10',
                   ALLOWED, ALLOWED, ALLOWED)

    def test_overshoot_options_are_open_to_all_roles(self):
        self.sweep('get', '/api/overshoot-options/', ALLOWED, ALLOWED, ALLOWED)

    def test_recipe_list_is_open_to_all_roles(self):
        self.sweep('get', '/api/recipe-master/', ALLOWED, ALLOWED, ALLOWED)

    def test_recipe_export_is_open_to_all_roles(self):
        self.sweep('get', '/api/recipe-export-master/', ALLOWED, ALLOWED, ALLOWED)

    def test_excel_export_is_open_to_all_roles(self):
        self.sweep('get',
                   '/api/changeover-export/?from_date=2026-04-01&to_date=2026-04-10',
                   ALLOWED, ALLOWED, ALLOWED)

    def test_kepware_export_is_open_to_all_roles(self):
        self.sweep('get', '/api/kepware-logs-export/?date=2026-04-07',
                   ALLOWED, ALLOWED, ALLOWED)

    def test_pdf_report_is_open_to_all_roles(self):
        self.sweep('get',
                   '/api/reports/summary/?start_date=2026-04-01&end_date=2026-04-10',
                   ALLOWED, ALLOWED, ALLOWED)

    # -------------------------------------- shift incharge and above write

    def test_recording_a_reason_excludes_operators(self):
        self.sweep('patch', f'/api/changeover/update/{self.summary.id}/',
                   FORBIDDEN, ALLOWED, ALLOWED,
                   payload={'overshoot': self.reason.id})

    # ------------------------------------------------------ managers only

    def test_recipe_creation_is_manager_only(self):
        self.sweep('post', '/api/recipe-master/',
                   FORBIDDEN, FORBIDDEN, ALLOWED,
                   payload={'recipe_code': 'NEWONE', 'recipe_type': 'fabric',
                            'target_speed': 25.0})

    def test_recipe_update_is_manager_only(self):
        self.sweep('patch', f'/api/recipe-master/{self.recipe.recipe_code}/',
                   FORBIDDEN, FORBIDDEN, ALLOWED, payload={'target_speed': 44.0})

    def test_recipe_upload_is_manager_only(self):
        self.sweep('post', '/api/recipe-upload/', FORBIDDEN, FORBIDDEN, ALLOWED)

    def test_standard_time_list_is_manager_only(self):
        self.sweep('get', '/api/standard-time/', FORBIDDEN, FORBIDDEN, ALLOWED)

    def test_standard_time_creation_is_manager_only(self):
        self.sweep('post', '/api/standard-time/', FORBIDDEN, FORBIDDEN, ALLOWED,
                   payload={'changeover_key': 'NEW KEY', 'standard_time': 12.0})

    def test_standard_time_delete_is_manager_only(self):
        self.sweep('delete', f'/api/standard-time/{self.standard.id}/',
                   FORBIDDEN, FORBIDDEN, ALLOWED)

    def test_standard_time_rules_are_manager_only(self):
        self.sweep('post', f'/api/standard-time/{self.standard.id}/rules/',
                   FORBIDDEN, FORBIDDEN, ALLOWED,
                   payload={'from_recipe': 'A', 'to_recipe': 'B'})

    def test_missing_recipe_warnings_are_manager_only(self):
        self.sweep('get', '/api/missing-recipes-warning/',
                   FORBIDDEN, FORBIDDEN, ALLOWED)

    def test_correction_request_list_is_for_approvers(self):
        # Shift incharges approve too: they run the shift the claim is
        # about. Operators raise requests, they do not decide them.
        self.sweep('get', '/api/correction-requests/',
                   FORBIDDEN, ALLOWED, ALLOWED)

    def test_correction_request_action_is_for_approvers(self):
        self.sweep('post', '/api/correction-requests/1/action/',
                   FORBIDDEN, ALLOWED, ALLOWED, payload={'action': 'approve'})
