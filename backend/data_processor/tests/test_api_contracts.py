"""API contract: status codes, payload shape and filter behaviour.

Documentation section 4 lists the endpoints and their access levels. These
tests cover what each one returns; authorization is covered separately.

Three tests here are REGRESSION tests for defects confirmed on a running
instance. They are marked RG-01 to RG-03 and each fails until the defect is
fixed -- that is deliberate. See the class docstrings.
"""

import datetime

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .base import fast_passwords, CeleryEagerMixin, RealDatabaseGuard, UnmanagedTableMixin

from data_processor.models import (
    ChangeoverSummary,
    OvershootReasons,
    RecipeMaster,
    StandardTimeMaster,
)

User = get_user_model()


def make_user(employee_id='20002', role=None, name='Test User'):
    return User.objects.create_user(
        employee_id=employee_id, password='testpass123', name=name,
        role=role or User.Role.OPERATOR)


@fast_passwords
class ApiTestBase(APITestCase):
    """Authenticated client plus a small, predictable dataset."""

    def setUp(self):
        self.user = make_user('20003', User.Role.MANAGER, 'P Mehta')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        RecipeMaster.objects.create(
            recipe_code='CP 034 HI', target_speed=20.0, recipe_type='steel')
        RecipeMaster.objects.create(
            recipe_code='BRTTL', target_speed=40.0, recipe_type='fabric')
        StandardTimeMaster.objects.create(
            changeover_key='Steel to Fabric', standard_time=30.0, is_custom=False)
        self.reason = OvershootReasons.objects.create(
            category='Mechanical', reason='Die change took longer')

        # A changeover at 10:00 on 2026-04-05 -> shift A, production day the 5th.
        self.summary = ChangeoverSummary.objects.create(
            batch=1001,
            recipe_change_time=datetime.datetime(2026, 4, 5, 10, 0),
            setup_complete_timestamp=datetime.datetime(2026, 4, 5, 10, 40),
            previous_recipe='CP 034 HI',
            current_recipe='BRTTL',
            current_type='fabric',
            previous_type='steel',
            change_over_type='Steel to Fabric',
            setup_time_actual=40.0,
            standard_time=30,
            static_setup_time=25.0,
            ramp_up_time=15.0,
            shift='A',
            production_date=datetime.date(2026, 4, 5),
        )


@fast_passwords
class ChangeoverStatsContractTests(ApiTestBase):
    """GET /api/changeover-stats/"""

    URL = '/api/changeover-stats/'

    def test_valid_range_returns_both_payload_keys(self):
        response = self.client.get(
            self.URL, {'from_date': '2026-04-01', 'to_date': '2026-04-10'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('table_data', response.data)
        self.assertIn('bar_chart_data', response.data)

    def test_rows_are_grouped_by_changeover_type(self):
        response = self.client.get(
            self.URL, {'from_date': '2026-04-01', 'to_date': '2026-04-10'})
        types = {row['type'] for row in response.data['table_data']}
        self.assertIn('Steel to Fabric', types)

    def test_overshoot_is_actual_minus_standard(self):
        # Documentation section 3.5: Overshoot = Actual - Standard.
        response = self.client.get(
            self.URL, {'from_date': '2026-04-01', 'to_date': '2026-04-10'})
        row = next(r for r in response.data['table_data']
                   if r['type'] == 'Steel to Fabric')
        self.assertAlmostEqual(row['shoot'], row['act'] - row['Std'], places=1)

    def test_range_with_no_data_returns_empty_not_404(self):
        response = self.client.get(
            self.URL, {'from_date': '2020-01-01', 'to_date': '2020-01-02'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['table_data'], [])

    def test_malformed_date_returns_400(self):
        response = self.client.get(
            self.URL, {'from_date': 'not-a-date', 'to_date': '2026-04-10'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reversed_range_returns_empty_not_an_error(self):
        response = self.client.get(
            self.URL, {'from_date': '2026-04-10', 'to_date': '2026-04-01'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['table_data'], [])

    def test_shift_filter_matches(self):
        response = self.client.get(
            self.URL,
            {'from_date': '2026-04-01', 'to_date': '2026-04-10', 'shift': 'A'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['table_data'])

    def test_shift_filter_excludes_other_shifts(self):
        response = self.client.get(
            self.URL,
            {'from_date': '2026-04-01', 'to_date': '2026-04-10', 'shift': 'C'})
        self.assertEqual(response.data['table_data'], [])

    def test_production_day_window_runs_7am_to_7am(self):
        # Section 2.10: shift C crosses midnight, so a production day runs
        # 07:00 to 07:00. A changeover at 02:00 on the 6th belongs to the 5th.
        ChangeoverSummary.objects.create(
            batch=1002,
            recipe_change_time=datetime.datetime(2026, 4, 6, 2, 0),
            previous_recipe='BRTTL', current_recipe='CP 034 HI',
            change_over_type='Fabric to Steel',
            setup_time_actual=20.0, standard_time=30,
            shift='C', production_date=datetime.date(2026, 4, 5),
        )
        response = self.client.get(
            self.URL, {'from_date': '2026-04-05', 'to_date': '2026-04-05'})
        types = {row['type'] for row in response.data['table_data']}
        self.assertIn('Fabric to Steel', types)


@fast_passwords
class ChangeoverStatsDefaultWindowTests(ApiTestBase):
    """RG-01 regression guard.

    GET /api/changeover-stats/ with no date parameters used to return 500:
    "localtime() cannot be applied to a naive datetime". USE_TZ is False, so
    timezone.now() returns a naive datetime that timezone.localtime() rejects.
    Fixed by using datetime.datetime.now(), which is the correct call for a
    USE_TZ=False project -- NOT by flipping USE_TZ, which would shift every
    stored timestamp by 5:30 and reassign shifts across the whole history.
    """

    def test_no_date_params_returns_200(self):
        response = self.client.get('/api/changeover-stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@fast_passwords
class OvershootOptionsContractTests(ApiTestBase):
    """GET /api/overshoot-options/"""

    def test_returns_all_categories_and_reasons(self):
        OvershootReasons.objects.create(category='Electrical', reason='Power fluctuation')
        response = self.client.get('/api/overshoot-options/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertIn('category', response.data[0])
        self.assertIn('reason', response.data[0])


@fast_passwords
class RecipeMasterContractTests(ApiTestBase):
    """GET /api/recipe-master/ and the export."""

    def test_list_returns_every_recipe(self):
        response = self.client.get('/api/recipe-master/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_export_as_json(self):
        response = self.client.get('/api/recipe-export-master/', {'format': 'json'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_this_endpoint_serves_json_only(self):
        # Documentation section 4.2 advertises "json, xls, csv using
        # ?format = excel". That never worked: `format` is a RESERVED DRF
        # content-negotiation parameter, so an unrecognised renderer name
        # returns 404 before the view body runs. The CSV and Excel branches
        # here were unreachable and have been removed.
        #
        # The spreadsheet exports the UI actually offers are
        # /api/changeover-export/ and /api/kepware-logs-export/, neither of
        # which takes a `format` parameter. Both are covered below.
        for fmt in ('csv', 'excel', 'xlsx'):
            with self.subTest(fmt=fmt):
                response = self.client.get(
                    '/api/recipe-export-master/', {'format': fmt})
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_default_request_returns_the_recipe_map(self):
        response = self.client.get('/api/recipe-export-master/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('CP 034 HI', response.data)


@fast_passwords
class ExportContractTests(UnmanagedTableMixin, ApiTestBase):
    """Excel and Kepware exports.

    Kepware reads RawLineData, whose table is managed=False, so this class
    needs the unmanaged-table fixture.
    """

    def test_changeover_export_returns_a_file(self):
        response = self.client.get(
            '/api/changeover-export/',
            {'from_date': '2026-04-01', 'to_date': '2026-04-10'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('attachment', response.get('Content-Disposition', ''))

    def test_kepware_export_with_no_logs_returns_404(self):
        response = self.client.get('/api/kepware-logs-export/', {'date': '2020-01-01'})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_kepware_export_rejects_a_malformed_date(self):
        response = self.client.get('/api/kepware-logs-export/', {'date': 'nonsense'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@fast_passwords
class AuthenticationContractTests(APITestCase):
    """Token handling on the DRF endpoints."""

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get('/api/changeover-stats/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_bad_token_returns_401_not_500(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token deadbeefdeadbeef')
        response = client.get('/api/changeover-stats/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_returns_a_token_and_the_role(self):
        make_user('30001', User.Role.MANAGER, 'L Manager')
        response = APIClient().post(
            '/api/auth/login/',
            {'employee_id': '30001', 'password': 'testpass123'},
            format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)
        self.assertEqual(response.data['user']['role'], 'manager')
        self.assertEqual(response.data['user']['employee_id'], '30001')

    def test_login_with_a_wrong_password_returns_400(self):
        make_user('30002')
        response = APIClient().post(
            '/api/auth/login/',
            {'employee_id': '30002', 'password': 'wrongpassword'},
            format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_there_is_no_public_signup_endpoint(self):
        # Accounts come from the employee roster only. The old open signup
        # is gone; /api/auth/claim/ activates an existing roster account but
        # cannot create one.
        response = APIClient().post(
            '/api/auth/signup/',
            {'employee_id': '99999', 'password': 'testpass123'},
            format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_me_returns_the_signed_in_user(self):
        user = make_user('30003', User.Role.SHIFT_INCHARGE, 'S Incharge')
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['employee_id'], '30003')
        self.assertEqual(response.data['role'], 'shift_incharge')


@fast_passwords
class ReportsSummaryTests(ApiTestBase):
    """RG-02 and RG-03 regression guards.

    RG-02: this endpoint used to return 500 for EVERY request. The bare
    timezone.localtime() call ran unconditionally, above the parameter check,
    so no combination of parameters could produce a PDF.

    RG-03: it was also a plain Django function view with no DRF decorator, so
    DEFAULT_PERMISSION_CLASSES never applied and anyone who could reach the
    host could pull the production report. It now uses @api_view and
    @permission_classes([IsAuthenticated]), matching every other view here.

    These had to be fixed together: fixing RG-02 alone would have turned a
    broken endpoint into a working unauthenticated data leak.
    """

    def test_authenticated_request_returns_a_pdf(self):
        response = self.client.get(
            '/api/reports/summary/',
            {'start_date': '2026-04-01', 'end_date': '2026-04-10'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get('/api/reports/summary/')
        self.assertIn(response.status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_an_accept_pdf_header_is_honoured(self):
        # RG-04 regression.
        #
        # @api_view brings DRF content negotiation, and its default renderers
        # offer only application/json and text/html. The dashboard asks for
        # `Accept: application/pdf`, which matched nothing and returned 406
        # Not Acceptable before the view ran -- so adding authentication here
        # silently broke the Generate Report button.
        #
        # The earlier test missed it because the test client sends no Accept
        # header, and DRF then just picks its first renderer.
        response = self.client.get(
            '/api/reports/summary/',
            {'start_date': '2026-04-01', 'end_date': '2026-04-10'},
            HTTP_ACCEPT='application/pdf')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_every_reasonable_accept_header_works(self):
        for accept in ('application/pdf', 'application/json', '*/*',
                       'text/html,application/xhtml+xml,*/*;q=0.8'):
            with self.subTest(accept=accept):
                response = self.client.get(
                    '/api/reports/summary/',
                    {'start_date': '2026-04-01', 'end_date': '2026-04-10'},
                    HTTP_ACCEPT=accept)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_the_response_is_actually_a_pdf(self):
        response = self.client.get(
            '/api/reports/summary/',
            {'start_date': '2026-04-01', 'end_date': '2026-04-10'},
            HTTP_ACCEPT='application/pdf')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.getvalue().startswith(b'%PDF'))


class OvershootReasonRoundTripTests(RealDatabaseGuard, APITestCase):
    """A saved overshoot reason has to come back readable.

    `overshoot` is write_only, so the id a client PATCHes in was never
    returned. The dashboard's reason dropdown had nothing to select and
    fell back to "Select Reason" every time the panel reopened, even
    though the reason was stored -- and the category came back correctly
    from its own read-only field, which made it look as though the reason
    alone had failed to save.
    """

    def setUp(self):
        super().setUp()
        self.manager = get_user_model().objects.create_user(
            employee_id='MGR1', password='line-pass-99', name='Manager',
            role=get_user_model().Role.MANAGER)
        self.reason = OvershootReasons.objects.create(
            category='Method', reason='Trial run repeated')
        # A reason can only be recorded where there was an overshoot, so
        # the actual time has to exceed the standard.
        self.changeover = ChangeoverSummary.objects.create(
            batch=1.0,
            recipe_change_time=datetime.datetime(2026, 11, 8, 8, 0),
            setup_start_timestamp=datetime.datetime(2026, 11, 8, 8, 0),
            setup_complete_timestamp=datetime.datetime(2026, 11, 8, 9, 0),
            setup_time_actual=60.0, standard_time=45,
            shift='A', production_date=datetime.date(2026, 11, 8))

        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def _save_reason(self):
        return self.client.patch(
            f'/api/changeover/update/{self.changeover.pk}/',
            {'overshoot': self.reason.pk}, format='json')

    def _read_back(self):
        """The changeover as the dashboard receives it.

        changeover-stats groups rows by changeover type: table_data is a
        list of groups, each with a 'details' list of the real rows.
        """
        response = self.client.get(
            '/api/changeover-stats/?start_date=2026-11-08&end_date=2026-11-08')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for group in response.data['table_data']:
            for row in group['details']:
                if row['id'] == self.changeover.pk:
                    return row
        return None

    def test_the_reason_is_saved(self):
        self.assertEqual(self._save_reason().status_code, 200)
        self.changeover.refresh_from_db()
        self.assertEqual(self.changeover.overshoot, self.reason)

    def test_the_saved_reason_id_comes_back(self):
        # The dropdown needs the id, not just the label, to preselect.
        self._save_reason()
        row = self._read_back()

        self.assertIsNotNone(row, 'changeover missing from the stats payload')
        self.assertEqual(row['overshoot_id'], self.reason.pk)

    def test_the_label_fields_still_come_back(self):
        self._save_reason()
        row = self._read_back()

        self.assertEqual(row['overshoot_category'], 'Method')
        self.assertEqual(row['overshoot_reason'], 'Trial run repeated')

    def test_an_unset_reason_reads_as_null(self):
        row = self._read_back()
        self.assertIsNone(row['overshoot_id'])
