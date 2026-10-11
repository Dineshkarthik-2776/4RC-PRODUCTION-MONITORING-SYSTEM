"""Create the one bootstrap administrator account.

Every other account is created from inside the app: managers add operators
and shift incharges, and the administrator adds managers. This command exists
only to create the first account, so there is somebody to log in as.

    python manage.py seed_admin_user
    python manage.py seed_admin_user --employee-id admin_jk --password <secret>

Re-running is safe. If the account already exists it is left alone unless
--reset-password is passed, so a deploy script can call this every time.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user_authentication.models import User, normalize_employee_id

DEFAULT_EMPLOYEE_ID = 'admin_jk'
DEFAULT_PASSWORD = 'jktyres'
DEFAULT_NAME = 'JK Tyre Administrator'


class Command(BaseCommand):
    help = 'Create the bootstrap administrator account.'

    def add_arguments(self, parser):
        parser.add_argument('--employee-id', default=DEFAULT_EMPLOYEE_ID)
        parser.add_argument('--password', default=DEFAULT_PASSWORD)
        parser.add_argument('--name', default=DEFAULT_NAME)
        parser.add_argument(
            '--reset-password', action='store_true',
            help='Reset the password if the account already exists.')

    def handle(self, *args, **options):
        employee_id = normalize_employee_id(options['employee_id'])
        password = options['password']

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
        if password == DEFAULT_PASSWORD:
            self.stdout.write(self.style.WARNING(
                'This account uses the default password. Change it after the '
                'first sign-in -- anyone with the source can read it.'))
