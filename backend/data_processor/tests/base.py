"""Shared infrastructure for integration tests.

Three guarantees, in order of importance:

1. Tests never touch the real database. Django already redirects to a
   throwaway `test_<name>` database, but RealDatabaseGuard asserts it on every
   test rather than trusting it, so a misconfiguration fails loudly instead of
   writing to production.

2. Tests never enqueue real Celery tasks. CeleryEagerMixin runs them inline
   against the test database instead of sending them to the broker.

   Nothing currently calls .delay() from inside a request or a signal -- the
   one thing that did, a critical-overshoot email alert, has been removed.
   The guard stays because the risk it covers is subtle: Celery tasks here
   take PRIMARY KEYS as arguments, and test-database ids restart at 1, so a
   task leaked from a test would make a live worker act on an unrelated
   production row.

3. The unmanaged tables exist. RawLineData ('FRC') and MaterialMaster ('bom')
   are managed=False, so Django's test runner does not create them and any
   query against them fails with 'relation "FRC" does not exist'.
"""

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from data_processor.models import MaterialMaster, RawLineData


# Django's default PBKDF2 hasher is deliberately slow. Nothing here tests
# password hashing, so tests that create users use a fast one.
fast_passwords = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


class RealDatabaseGuard:
    """Refuse to run unless we are pointed at a throwaway test database."""

    @classmethod
    def assert_test_database(cls):
        name = str(connection.settings_dict.get('NAME', ''))
        # Django names test databases "test_<name>". SQLite in-memory test
        # databases are the exception -- they are named ':memory:' or
        # 'file:memorydb_<alias>?mode=memory&cache=shared' and are inherently
        # throwaway.
        in_memory = name == ':memory:' or 'mode=memory' in name
        if not (name.startswith('test_') or in_memory):
            raise RuntimeError(
                f'Refusing to run: connected to {name!r}, which is not a test '
                'database. Django names test databases "test_<name>". Run via '
                '`manage.py test`, never against a live settings module.'
            )
        return name

    def setUp(self):
        super().setUp()
        self.assert_test_database()


class CeleryEagerMixin:
    """Run Celery tasks inline so nothing reaches a real broker."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from backend.celery import app as celery_app

        cls._celery_app = celery_app
        cls._celery_previous = {
            'task_always_eager': celery_app.conf.task_always_eager,
            'task_eager_propagates': celery_app.conf.task_eager_propagates,
            'broker_url': celery_app.conf.broker_url,
        }
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        celery_app.conf.broker_url = 'memory://'

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._celery_previous.items():
            setattr(cls._celery_app.conf, key, value)
        super().tearDownClass()


class UnmanagedTableMixin:
    """Create the managed=False tables in the test database.

    Built from the model definitions rather than the shipped DDL, so the test
    table matches what Django believes the table is. That is correct for
    behavioural tests but cannot catch schema drift -- those checks must
    introspect the real database separately.
    """

    unmanaged_models = (RawLineData, MaterialMaster)

    # Known limitation: this does not work on SQLite, which refuses to run the
    # schema editor inside a TestCase's atomic block ("cannot be used while
    # foreign key constraint checks are enabled"). PostgreSQL (dev) and MSSQL
    # (production) are unaffected. Use IntegrationTransactionTestCase if a
    # SQLite run is ever needed.

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        RealDatabaseGuard.assert_test_database()
        with connection.schema_editor() as schema_editor:
            for model in cls.unmanaged_models:
                schema_editor.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema_editor:
            for model in reversed(cls.unmanaged_models):
                schema_editor.delete_model(model)
        super().tearDownClass()


class IntegrationTestCase(
    RealDatabaseGuard, CeleryEagerMixin, UnmanagedTableMixin, TestCase
):
    """Base class for pipeline integration tests.

    Each test runs inside a transaction that is rolled back, so tests cannot
    see each other's rows.
    """


class IntegrationTransactionTestCase(
    RealDatabaseGuard, CeleryEagerMixin, UnmanagedTableMixin, TransactionTestCase
):
    """For tests that need real commits -- transaction.on_commit callbacks,
    select_for_update, or the task's own atomic blocks.

    Slower, and it truncates tables between tests instead of rolling back.
    Prefer IntegrationTestCase unless a test genuinely needs commit behaviour.
    """
