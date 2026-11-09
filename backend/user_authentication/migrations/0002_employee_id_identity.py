"""Switch user identity from email to the company employee ID.

Existing rows are kept rather than deleted -- ChangeoverCorrectionRequest
has a CASCADE foreign key to this table, so deleting would take that history
with it. They are given LEGACY-nnnn placeholder IDs and marked RETIRED: the
row survives for attribution, the account can never sign in again.

Run `manage.py retire_legacy_users` AFTER this migration, not before -- that
command queries through the Django model, so the schema has to match it
already. This migration retires every surviving row; the command then purges
them outright when there is no correction-request history worth keeping.

Old roles map on: admin and manager both become MANAGER (an administrator is
a manager carrying is_superuser), worker becomes OPERATOR.
"""

from django.contrib.auth.hashers import make_password
from django.db import migrations, models

ROLE_MAP = {
    'admin': 'manager',
    'manager': 'manager',
    'worker': 'operator',
}


def assign_legacy_identities(apps, schema_editor):
    User = apps.get_model('user_authentication', 'User')
    for index, user in enumerate(User.objects.all().order_by('id'), start=1):
        user.employee_id = f'LEGACY-{index:04d}'
        user.role = ROLE_MAP.get(user.role, 'operator')
        user.status = 'retired'
        user.is_active = False
        # Belt and braces. is_active=False already blocks authentication, but
        # an unusable password means the account still cannot be signed into
        # if someone flips is_active back later.
        user.password = make_password(None)
        user.save(update_fields=[
            'employee_id', 'role', 'status', 'is_active', 'password'])


def restore_placeholder_emails(apps, schema_editor):
    """Reverse step: the original emails are gone, so synthesise one.

    This exists so the migration is reversible for local experimentation. It
    does NOT recover the original addresses -- there is nowhere to recover
    them from.
    """
    User = apps.get_model('user_authentication', 'User')
    for user in User.objects.all():
        user.email = f'{user.employee_id.lower()}@legacy.invalid'
        user.save(update_fields=['email'])


class Migration(migrations.Migration):

    dependencies = [
        ('user_authentication', '0001_initial'),
    ]

    operations = [
        # 1. Add the new columns, nullable for now so existing rows survive.
        migrations.AddField(
            model_name='user',
            name='employee_id',
            field=models.CharField(max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='status',
            field=models.CharField(
                choices=[('pending', 'Pending claim'), ('active', 'Active'),
                         ('retired', 'Retired')],
                default='pending', max_length=10,
                help_text='PENDING until the employee claims the account; '
                          'RETIRED accounts are kept for history but can '
                          'never sign in.'),
        ),

        # 2. Give the surviving rows an identity.
        migrations.RunPython(assign_legacy_identities, restore_placeholder_emails),

        # 3. Now it can carry the unique constraint.
        migrations.AlterField(
            model_name='user',
            name='employee_id',
            field=models.CharField(
                db_index=True, max_length=32, unique=True,
                help_text='The company-issued employee ID. Used to sign in.'),
        ),

        # 4. Email is no longer part of identity, or of anything else.
        migrations.RemoveField(model_name='user', name='email'),

        # 5. The three roles the business actually has.
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('manager', 'Manager'),
                         ('shift_incharge', 'Shift Incharge'),
                         ('operator', 'Operator')],
                default='operator', max_length=20),
        ),

        # 6. Accounts start inactive and are activated by being claimed.
        migrations.AlterField(
            model_name='user',
            name='is_active',
            field=models.BooleanField(
                default=False,
                help_text='Authentication gate. False until the account is '
                          'claimed.'),
        ),

        migrations.AlterModelOptions(
            name='user', options={'ordering': ['employee_id']},
        ),
    ]
