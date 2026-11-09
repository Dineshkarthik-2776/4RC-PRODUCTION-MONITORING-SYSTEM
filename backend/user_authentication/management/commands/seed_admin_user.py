"""Create the one bootstrap administrator account.

Every other account is created from inside the app: managers add operators
and shift incharges, and the administrator adds managers. This command exists
only to create the first account, so there is somebody to log in as.

    ADMIN_SEED_PASSWORD=<secret> python manage.py seed_admin_user
    python manage.py seed_admin_user --password <secret>

The password must be supplied, by --password or by ADMIN_SEED_PASSWORD.
There is deliberately no default: this repository is public, and a
built-in administrator password is one anybody can read.

Re-running is safe. If the account already exists it is left alone unless
--reset-password is passed, so a deploy script can call this every time.
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user_authentication.models import User, normalize_employee_id

DEFAULT_EMPLOYEE_ID = 'admin_jk'
DEFAULT_NAME = 'JK Tyre Administrator'
PASSWORD_ENV_VAR = 'ADMIN_SEED_PASSWORD'


class Command(BaseCommand):
    help = 'Create the bootstrap administrator account.'

    def add_arguments(self, parser):
        parser.add_argument('--employee-id', default=DEFAULT_EMPLOYEE_ID)
        parser.add_argument(
            '--password',
            help='Falls back to the ADMIN_SEED_PASSWORD environment variable.')
        parser.add_argument('--name', default=DEFAULT_NAME)
        parser.add_argument(
            '--reset-password', action='store_true',
            help='Reset the password if the account already exists.')

    def handle(self, *args, **options):
        employee_id = normalize_employee_id(options['employee_id'])
        password = options['password'] or os.environ.get(PASSWORD_ENV_VAR)
        if not password:
            raise CommandError(
                'No password given. Pass --password, or set '
                f'{PASSWORD_ENV_VAR}. There is no default on purpose -- a '
                'built-in administrator password is one anybody with the '
                'source can read.')

        with transaction.atomic():
            user = User.objects.filter(employee_id=employee_id).first()

            if user is not None:
                if options['reset_password']:
                    user.set_password(password)
                    user.status = User.Status.ACTIVE
                    user.is_active = True
                    user.is_staff = True
                    user.is_superuser = True
                    user.role = User.Role.MANAGER
                    user.save()
                    self.stdout.write(self.style.SUCCESS(
                        f'Reset the administrator account {employee_id}.'))
                else:
                    self.stdout.write(
                        f'{employee_id} already exists. Nothing changed. '
                        'Pass --reset-password to reset it.')
                return

            User.objects.create_superuser(
                employee_id=employee_id, password=password,
                name=options['name'])

        self.stdout.write(self.style.SUCCESS(
            f'Created administrator {employee_id}.'))
        self.stdout.write(
            'Sign in with it once and change it from the Users panel.')
