"""Import the company employee roster.

Creates one PENDING account per employee. Nobody can sign in until they claim
their own account and choose a password, so this command never sets or knows
a password.

The CSV needs employee_id, name and role columns:

    employee_id,name,role
    10432,A Kumar,operator
    10433,S Raman,shift_incharge
    10001,P Mehta,manager

Role accepts the stored values (operator, shift_incharge, manager) and the
obvious spellings people type -- "Shift Incharge", "shift-incharge",
"SHIFT INCHARGE" all land on shift_incharge.

Re-running is safe: existing employees are matched on the normalised ID and
updated in place, never duplicated. A claimed account keeps its password and
status; only name and role are refreshed, so an HR change flows through
without locking anyone out.

    python manage.py import_employee_roster roster.csv --dry-run
    python manage.py import_employee_roster roster.csv
    python manage.py import_employee_roster roster.csv --admin 10001
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user_authentication.models import User, normalize_employee_id

REQUIRED_COLUMNS = {'employee_id', 'name', 'role'}

ROLE_ALIASES = {
    'manager': User.Role.MANAGER,
    'shiftincharge': User.Role.SHIFT_INCHARGE,
    'shift_incharge': User.Role.SHIFT_INCHARGE,
    'shiftin-charge': User.Role.SHIFT_INCHARGE,
    'incharge': User.Role.SHIFT_INCHARGE,
    'operator': User.Role.OPERATOR,
}


def parse_role(raw):
    key = str(raw or '').strip().lower().replace(' ', '').replace('-', '')
    key = key.replace('_', '') or ''
    for alias, role in ROLE_ALIASES.items():
        if alias.replace('_', '').replace('-', '') == key:
            return role
    return None


class Command(BaseCommand):
    help = 'Import the employee roster as PENDING accounts.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', help='Path to the roster CSV.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Validate and report without writing anything.')
        parser.add_argument(
            '--admin', metavar='EMPLOYEE_ID',
            help='Grant is_staff/is_superuser to this employee, who must be '
                 'a manager in the roster. This is how an administrator is '
                 'made -- there is no admin role.')

    # ------------------------------------------------------------- reading

    def _read(self, csv_path):
        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f'No such file: {csv_path}')

        with path.open(newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            columns = {c.strip().lower() for c in (reader.fieldnames or [])}
            missing = REQUIRED_COLUMNS - columns
            if missing:
                raise CommandError(
                    f'Missing column(s): {", ".join(sorted(missing))}. '
                    f'Found: {", ".join(sorted(columns)) or "nothing"}')
            return [
                {(k or '').strip().lower(): (v or '').strip()
                 for k, v in row.items()}
                for row in reader
            ]

    def _validate(self, rows):
        employees, problems, seen = [], [], {}

        for number, row in enumerate(rows, start=2):   # row 1 is the header
            employee_id = normalize_employee_id(row.get('employee_id'))
            name = (row.get('name') or '').strip()
            role = parse_role(row.get('role'))

            if not employee_id:
                problems.append(f'row {number}: employee_id is empty')
                continue
            if not name:
                problems.append(f'row {number}: name is empty')
                continue
            if role is None:
                problems.append(
                    f'row {number}: unrecognised role {row.get("role")!r} '
                    f'(expected manager, shift_incharge or operator)')
                continue
            if employee_id in seen:
                problems.append(
                    f'row {number}: duplicate employee_id {employee_id} '
                    f'(first seen on row {seen[employee_id]})')
                continue

            seen[employee_id] = number
            employees.append({'employee_id': employee_id, 'name': name,
                              'role': role})
        return employees, problems

    # -------------------------------------------------------------- action

    def handle(self, *args, **options):
        rows = self._read(options['csv_path'])
        employees, problems = self._validate(rows)

        self.stdout.write(f'Read {len(rows)} row(s) from {options["csv_path"]}.')

        if problems:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR(f'{len(problems)} problem(s):'))
            for problem in problems[:20]:
                self.stdout.write(f'  {problem}')
            if len(problems) > 20:
                self.stdout.write(f'  ... and {len(problems) - 20} more')
            raise CommandError('Roster has problems. Nothing was imported.')

        existing = {u.employee_id: u for u in User.objects.all()}
        to_create = [e for e in employees if e['employee_id'] not in existing]
        to_update = [e for e in employees if e['employee_id'] in existing]

        by_role = {}
        for employee in employees:
            by_role[employee['role']] = by_role.get(employee['role'], 0) + 1

        self.stdout.write('')
        self.stdout.write(f'  to create : {len(to_create)}')
        self.stdout.write(f'  to update : {len(to_update)} (name and role only)')
        for role, count in sorted(by_role.items()):
            self.stdout.write(f'    {role:<16} {count}')

        admin_id = normalize_employee_id(options['admin']) if options['admin'] else None
        if admin_id:
            match = next((e for e in employees if e['employee_id'] == admin_id), None)
            if match is None:
                raise CommandError(
                    f'--admin {admin_id} is not in the roster.')
            if match['role'] != User.Role.MANAGER:
                raise CommandError(
                    f'--admin {admin_id} has role {match["role"]}. An '
                    'administrator must be a manager.')
            self.stdout.write('')
            self.stdout.write(
                f'  admin     : {admin_id} ({match["name"]}) gets '
                'is_staff + is_superuser')

        if options['dry_run']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'DRY RUN -- nothing was written.'))
            return

        created = updated = 0
        with transaction.atomic():
            for employee in employees:
                user = existing.get(employee['employee_id'])
                if user is None:
                    User.objects.create_roster_user(
                        employee_id=employee['employee_id'],
                        name=employee['name'],
                        role=employee['role'])
                    created += 1
                else:
                    # Never touch password or status: a claimed account must
                    # keep working across a roster refresh.
                    changed = []
                    if user.name != employee['name']:
                        user.name = employee['name']
                        changed.append('name')
                    if user.role != employee['role']:
                        user.role = employee['role']
                        changed.append('role')
                    if changed:
                        user.save(update_fields=changed)
                        updated += 1

            if admin_id:
                admin = User.objects.get(employee_id=admin_id)
                admin.is_staff = True
                admin.is_superuser = True
                admin.save(update_fields=['is_staff', 'is_superuser'])

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Created {created}, updated {updated}.'))
        self.stdout.write(
            'All new accounts are PENDING -- each employee sets their own '
            'password at first sign-in.')
