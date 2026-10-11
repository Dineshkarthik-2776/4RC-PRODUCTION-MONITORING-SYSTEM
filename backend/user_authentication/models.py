"""The 4RC user model.

Identity is the employee ID issued by the company, not an email address.
Every account originates from the employee roster rather than from
self-registration: the roster is imported as PENDING accounts, and an
employee turns their own account into a usable one by claiming it with a
password they choose. Nothing outside the roster can ever sign in.

Three roles, matching how the plant is organised:

    MANAGER         -- plant management; can override the correction window
                       and review correction requests
    SHIFT_INCHARGE  -- supervises a shift
    OPERATOR        -- runs the line; records overshoot reasons

"Admin" is not a fourth role. An administrator is a MANAGER whose is_staff
and is_superuser flags are set, which is what unlocks Django's /admin/. That
keeps the role list to the three the business actually has, and leaves
privilege to Django's own flags rather than a parallel system.
"""

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models


def normalize_employee_id(value):
    """Collapse an employee ID to its canonical form.

    IDs are typed by hand at a login screen and arrive from the company
    roster in whatever case and spacing the spreadsheet happened to use, so
    they are compared with spaces removed and upper-cased -- the same rule
    the recipe codes use elsewhere in this project.
    """
    return str(value or '').strip().replace(' ', '').upper()


class UserManager(BaseUserManager):

    def _create(self, employee_id, name, role, **extra_fields):
        employee_id = normalize_employee_id(employee_id)
        if not employee_id:
            raise ValueError('Employee ID is required')
        return self.model(
            employee_id=employee_id,
            name=(name or '').strip(),
            role=role,
            **extra_fields,
        )

    def create_roster_user(self, employee_id, name, role=None, **extra_fields):
        """Create a PENDING account from the roster.

        The account cannot be signed into until the employee claims it: it is
        inactive and carries no usable password.
        """
        extra_fields.setdefault('status', User.Status.PENDING)
        extra_fields.setdefault('is_active', False)
        user = self._create(
            employee_id, name, role or User.Role.OPERATOR, **extra_fields)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, employee_id, password=None, name='', role=None,
                    **extra_fields):
        """Create an account with a password already set.

        Used by tests and by management commands that provision an account
        directly. The ordinary path for a real employee is
        create_roster_user() followed by a claim.
        """
        extra_fields.setdefault('status', User.Status.ACTIVE)
        extra_fields.setdefault('is_active', True)
        user = self._create(
            employee_id, name, role or User.Role.OPERATOR, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, employee_id, password=None, name='',
                         **extra_fields):
        """An administrator: a manager carrying Django's privilege flags."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('status', User.Status.ACTIVE)
        extra_fields.setdefault('is_active', True)

        if not extra_fields.get('is_staff'):
            raise ValueError('A superuser must have is_staff=True')
        if not extra_fields.get('is_superuser'):
            raise ValueError('A superuser must have is_superuser=True')

        return self.create_user(
            employee_id, password=password, name=name,
            role=User.Role.MANAGER, **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(employee_id=normalize_employee_id(username))


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        MANAGER = 'manager', 'Manager'
        SHIFT_INCHARGE = 'shift_incharge', 'Shift Incharge'
        OPERATOR = 'operator', 'Operator'

    class Status(models.TextChoices):
        # One status covers both a brand-new account and one whose password
        # an administrator cleared: either way the person chooses a password
        # at next sign-in, so splitting them buys nothing.
        PENDING = 'pending', 'Awaiting password'
        ACTIVE = 'active', 'Active'
        # The employee has left. Permanent: the row survives so history and
        # upcoming features can still resolve who did what, but the account
        # is hidden from the app and can never sign in again.
        RETIRED = 'retired', 'Removed'

    employee_id = models.CharField(
        max_length=32, unique=True, db_index=True,
        help_text="The company-issued employee ID. Used to sign in.")
    name = models.CharField(max_length=255)
    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.OPERATOR)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
        help_text="PENDING until a password is set; RETIRED means the "
                  "employee has left -- kept for history, never signs in.")

    is_active = models.BooleanField(
        default=False,
        help_text="Authentication gate. False until the account is claimed.")
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = 'employee_id'
    REQUIRED_FIELDS = ['name']

    class Meta:
        ordering = ['employee_id']

    def __str__(self):
        return f"{self.employee_id} - {self.name} ({self.get_role_display()})"

    # ------------------------------------------------------------- helpers

    @property
    def is_manager(self):
        """Managers carry the elevated permissions in this system.

        Kept as a property so the rule lives in one place: the 5-hour
        correction override and the correction-request queue both consult it.
        """
        return self.role == self.Role.MANAGER

    @property
    def can_claim(self):
        """Only a roster account that nobody has claimed yet."""
        return self.status == self.Status.PENDING and not self.has_usable_password()

    def claim(self, password):
        """Turn a PENDING roster account into a usable one."""
        if not self.can_claim:
            raise ValueError('This account has already been claimed.')
        self.set_password(password)
        self.status = self.Status.ACTIVE
        self.is_active = True
        self.save(update_fields=['password', 'status', 'is_active'])
        return self

    def retire(self):
        """The employee has left.

        Permanent. The row is kept because correction history points at it
        and upcoming features need to resolve past users, but the account is
        hidden from the app and can never be signed into again.
        """
        self.status = self.Status.RETIRED
        self.is_active = False
        self.set_unusable_password()
        self.save(update_fields=['status', 'is_active', 'password'])
        return self
