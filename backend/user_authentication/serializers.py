"""Sign-in and account claiming.

There is no self-registration. Every account originates from the employee
roster, imported as PENDING. An employee turns their own account into a
usable one by claiming it with a password they choose; nothing outside the
roster can ever sign in.
"""

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import User, normalize_employee_id


class UserSummarySerializer(serializers.ModelSerializer):
    """What the client is told about the signed-in user."""

    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = User
        fields = ['employee_id', 'name', 'role', 'role_display']
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    employee_id = serializers.CharField()
    password = serializers.CharField(write_only=True)

    default_error_messages = {
        'invalid': 'Invalid employee ID or password.',
        'unclaimed': 'This account has not been set up yet. Set a password to '
                     'activate it.',
        'retired': 'This account is no longer active. Contact your manager.',
    }

    def validate(self, data):
        employee_id = normalize_employee_id(data.get('employee_id'))
        password = data.get('password')

        if not employee_id or not password:
            self.fail('invalid')

        user = authenticate(employee_id=employee_id, password=password)
        if user is not None:
            data['user'] = user
            return data

        # authenticate() returns None for a wrong password, an inactive
        # account and an unknown ID alike. Distinguish the cases that are
        # actionable for the employee, without confirming whether a password
        # was right.
        existing = User.objects.filter(employee_id=employee_id).first()
        if existing is not None:
            if existing.status == User.Status.RETIRED:
                self.fail('retired')
            if existing.can_claim:
                self.fail('unclaimed')
        self.fail('invalid')


class ClaimAccountSerializer(serializers.Serializer):
    """First-time setup: an employee sets their own password.

    Only works against a PENDING roster account that nobody has claimed. A
    claimed, retired or unknown ID is refused with the same message, so this
    cannot be used to discover which IDs exist.
    """

    employee_id = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    default_error_messages = {
        'not_claimable': 'This employee ID cannot be set up. It may already '
                         'have a password, or it may not be on the roster. '
                         'Contact your manager.',
        'mismatch': 'The two passwords do not match.',
    }

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            self.fail('mismatch')

        employee_id = normalize_employee_id(data['employee_id'])
        user = User.objects.filter(employee_id=employee_id).first()
        if user is None or not user.can_claim:
            self.fail('not_claimable')

        try:
            validate_password(data['password'], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})

        data['user'] = user
        return data

    def save(self, **kwargs):
        user = self.validated_data['user']
        return user.claim(self.validated_data['password'])


class UserAdminSerializer(serializers.ModelSerializer):
    """How a manager sees and edits the people they administer."""

    role_display = serializers.CharField(source='get_role_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_admin = serializers.BooleanField(source='is_superuser', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'employee_id', 'name', 'role', 'role_display',
            'status', 'status_display', 'is_active', 'is_admin', 'last_login',
        ]
        read_only_fields = ['id', 'status', 'is_active', 'is_admin', 'last_login']


class UserCreateSerializer(serializers.ModelSerializer):
    """Add a person.

    No password is set here. The account starts PENDING and the employee
    chooses their own password the first time they sign in, exactly as the
    claim flow works everywhere else. That way nobody -- not even a manager --
    ever knows somebody else's password.
    """

    # Declared explicitly with no validators so DRF does not attach its own
    # UniqueValidator. That validator checks the RAW value and reports a
    # generic "already exists", which fires before validate_employee_id and
    # hides both the normalisation and the removed-employee explanation.
    employee_id = serializers.CharField(max_length=32, validators=[])

    class Meta:
        model = User
        fields = ['employee_id', 'name', 'role']

    def validate_employee_id(self, value):
        employee_id = normalize_employee_id(value)
        if not employee_id:
            raise serializers.ValidationError('Employee ID is required.')
        existing = User.objects.filter(employee_id=employee_id).first()
        if existing is not None:
            # Removed employees are hidden from the list, so without this the
            # administrator sees "already exists" for an ID they cannot find
            # anywhere on screen.
            if existing.status == User.Status.RETIRED:
                raise serializers.ValidationError(
                    f'{employee_id} belonged to an employee who has been '
                    'removed. That ID cannot be reused.')
            raise serializers.ValidationError(f'{employee_id} already exists.')
        return employee_id

    def validate_role(self, value):
        """Only the administrator may create another manager."""
        request = self.context.get('request')
        actor = getattr(request, 'user', None)
        if value == User.Role.MANAGER and not (actor and actor.is_superuser):
            raise serializers.ValidationError(
                'Only the administrator can create manager accounts.')
        return value

    def create(self, validated_data):
        return User.objects.create_roster_user(**validated_data)


class UserUpdateSerializer(serializers.ModelSerializer):
    """Change somebody's name or role."""

    class Meta:
        model = User
        fields = ['name', 'role']

    def validate_role(self, value):
        request = self.context.get('request')
        actor = getattr(request, 'user', None)
        if value == User.Role.MANAGER and not (actor and actor.is_superuser):
            raise serializers.ValidationError(
                'Only the administrator can grant the manager role.')
        if (self.instance and self.instance.role == User.Role.MANAGER
                and value != User.Role.MANAGER
                and not (actor and actor.is_superuser)):
            raise serializers.ValidationError(
                'Only the administrator can change a manager\'s role.')
        return value


class VerifyPasswordSerializer(serializers.Serializer):
    """Step one of changing your own password: prove you know the current one.

    Separate from the change itself so the UI can tell somebody their current
    password is wrong before asking them to invent a new one. The change
    endpoint re-checks it regardless -- passing this step is not a token that
    authorises anything.
    """

    password = serializers.CharField(write_only=True)

    default_error_messages = {'wrong': 'That is not your current password.'}

    def validate_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            self.fail('wrong')
        return value


class ChangeOwnPasswordSerializer(serializers.Serializer):
    """Change your own password, having supplied the current one."""

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    default_error_messages = {
        'wrong': 'That is not your current password.',
        'mismatch': 'The two new passwords do not match.',
        'same': 'The new password must be different from the current one.',
    }

    def validate(self, data):
        user = self.context['request'].user

        if not user.check_password(data['current_password']):
            self.fail('wrong')
        if data['new_password'] != data['confirm_password']:
            self.fail('mismatch')
        if data['new_password'] == data['current_password']:
            self.fail('same')

        try:
            validate_password(data['new_password'], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)})

        return data

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.status = User.Status.ACTIVE
        user.is_active = True
        user.save(update_fields=['password', 'status', 'is_active'])
        return user
