"""Prepare the user table for the employee-ID roster import.

Run this ONCE, AFTER applying migration 0002_employee_id_identity and
BEFORE importing the employee roster:

    manage.py migrate                  # assigns LEGACY-nnnn ids, retires rows
    manage.py retire_legacy_users      # purges them if there is no history
    manage.py import_employee_roster   # the real accounts

The order matters. This command queries through the Django model, so the
database schema has to match it already -- run it before the migration and it
fails with "column user_authentication_user.employee_id does not exist".

The old accounts are keyed on email. The new ones are keyed on employee ID,
so nothing carries over: every token is invalidated and everyone signs in
again regardless of which branch runs.

What it does depends on whether there is history worth keeping:

  No correction requests  -> PURGE.  Delete every user outright. Nothing
                             references them, so nothing is lost.

  Correction requests     -> RETIRE. Keep every user row, because
                             ChangeoverCorrectionRequest.requested_by points
                             at them and deleting would CASCADE those rows
                             away. The accounts are made unusable instead:
                             deactivated, password set unusable, tokens gone.

ChangeoverCorrectionRequest.requested_by is the only foreign key to the user
table in the whole project, which is why that one count decides it.

    python manage.py retire_legacy_users --dry-run
    python manage.py retire_legacy_users
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.authtoken.models import Token

from data_processor.models import ChangeoverCorrectionRequest

User = get_user_model()

PURGE = 'purge'
RETIRE = 'retire'


class Command(BaseCommand):
    help = ('Purge or retire pre-employee-ID accounts ahead of the roster '
            'import. Chooses based on whether correction-request history exists.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would happen without changing anything.',
        )
        parser.add_argument(
            '--no-input', action='store_true',
            help='Skip the confirmation prompt. For scripted deployments.',
        )
        parser.add_argument(
            '--keep-superusers', action='store_true',
            help='Retire superusers instead of deleting them, even when '
                 'purging. Use this if you need /admin/ access before the '
                 'roster import creates a new admin account.',
        )

    # ---------------------------------------------------------------- report

    def _describe(self, mode, users, superusers, correction_count, token_count,
                  keep_superusers):
        self.stdout.write('')
        self.stdout.write('Current state')
        self.stdout.write(f'  users                : {users}')
        self.stdout.write(f'  of which superusers  : {superusers}')
        self.stdout.write(f'  auth tokens          : {token_count}')
        self.stdout.write(f'  correction requests  : {correction_count}')
        self.stdout.write('')

        if mode == PURGE:
            self.stdout.write(self.style.WARNING('Mode: PURGE'))
            self.stdout.write(
                '  No correction requests exist, so no row references a user.')
            if keep_superusers and superusers:
                self.stdout.write(f'  {users - superusers} user(s) will be DELETED.')
                self.stdout.write(f'  {superusers} superuser(s) will be RETIRED instead '
                                  '(--keep-superusers).')
            else:
                self.stdout.write(f'  All {users} user(s) will be DELETED.')
        else:
            self.stdout.write(self.style.WARNING('Mode: RETIRE'))
            self.stdout.write(
                f'  {correction_count} correction request(s) reference users. '
                'Deleting would cascade those away, so every account is kept '
                'and made unusable instead.')
            self.stdout.write(f'  All {users} user(s): is_active=False, password '
                              'set unusable.')

        self.stdout.write(f'  All {token_count} auth token(s) will be deleted '
                          '-- everyone signs in again.')
        self.stdout.write('')

    # ---------------------------------------------------------------- action

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        keep_superusers = options['keep_superusers']

        users = User.objects.count()
        if users == 0:
            self.stdout.write(self.style.SUCCESS(
                'No users exist. Nothing to do -- go ahead and import the roster.'))
            return

        superusers = User.objects.filter(is_superuser=True).count()
        token_count = Token.objects.count()
        correction_count = ChangeoverCorrectionRequest.objects.count()
        mode = PURGE if correction_count == 0 else RETIRE

        self._describe(mode, users, superusers, correction_count, token_count,
                       keep_superusers)

        if mode == PURGE and superusers and not keep_superusers:
            self.stdout.write(self.style.WARNING(
                'Note: this removes every superuser, so /admin/ will be '
                'unreachable until the roster import creates one. Pass '
                '--keep-superusers to retire them instead of deleting.'))
            self.stdout.write('')

        if mode == RETIRE:
            self.stdout.write(self.style.WARNING(
                'Note: these rows keep the LEGACY-nnnn identifiers assigned by '
                'migration 0002. They stay only so correction-request history '
                'resolves to a name; none of them can sign in.'))
            self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                'DRY RUN -- nothing was changed. Re-run without --dry-run to apply.'))
            return

        if not options['no_input']:
            answer = input('This cannot be undone. Type "yes" to continue: ')
            if answer.strip().lower() != 'yes':
                raise CommandError('Aborted. Nothing was changed.')

        deleted, retired = self._apply(mode, keep_superusers)

        self.stdout.write('')
        if deleted:
            self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} user(s).'))
        if retired:
            self.stdout.write(self.style.SUCCESS(f'Retired {retired} user(s).'))
        self.stdout.write(self.style.SUCCESS(
            f'Revoked {token_count} auth token(s).'))
        self.stdout.write('Next step: import the employee roster.')

    def _apply(self, mode, keep_superusers):
        deleted = retired = 0
        with transaction.atomic():
            Token.objects.all().delete()

            if mode == PURGE:
                if keep_superusers:
                    retired = self._retire(User.objects.filter(is_superuser=True))
                    deleted, _ = User.objects.filter(is_superuser=False).delete()
                else:
                    deleted, _ = User.objects.all().delete()
            else:
                retired = self._retire(User.objects.all())
        return deleted, retired

    @staticmethod
    def _retire(queryset):
        """Deactivate and make the password unusable.

        Both matter. is_active=False stops authentication today; an unusable
        password means the account still cannot be signed into if someone
        flips is_active back later.
        """
        count = 0
        for user in queryset:
            user.is_active = False
            user.set_unusable_password()
            user.save(update_fields=['is_active', 'password'])
            count += 1
        return count
