from django.contrib.auth.models import update_last_login
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework import generics

from .models import User
from .permissions import IsAdmin, IsManager
from .shift_session import duty_payload, start_duty, stop_duty
from .serializers import (
    ChangeOwnPasswordSerializer,
    ClaimAccountSerializer,
    LoginSerializer,
    UserAdminSerializer,
    UserCreateSerializer,
    VerifyPasswordSerializer,
    UserSummarySerializer,
    UserUpdateSerializer,
)


class LoginView(APIView):
    """Sign in with an employee ID and password."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        update_last_login(None, user)

        # An operator signing in is an operator standing at the line, so
        # this is also the record of who was on duty. Other roles get no
        # duty session -- see data_processor.setup_capture.
        session = start_duty(user)

        return Response(
            {
                'message': 'Login successful',
                'token': token.key,
                'user': UserSummarySerializer(user).data,
                'duty': duty_payload(user, session),
            },
            status=status.HTTP_200_OK,
        )


class ClaimAccountView(APIView):
    """First-time setup for a roster account.

    Replaces the old public signup endpoint. Accounts cannot be created here
    -- only activated, and only if the employee ID is already on the roster.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ClaimAccountSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        session = start_duty(user)
        return Response(
            {
                'message': 'Account activated.',
                'token': token.key,
                'user': UserSummarySerializer(user).data,
                'duty': duty_payload(user, session),
            },
            status=status.HTTP_201_CREATED,
        )


class CurrentUserView(APIView):
    """Who am I -- lets the client refresh role and name without re-login.

    Also reports the duty session and when the shift ends, which is what
    the browser counts down to before signing an operator out.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = UserSummarySerializer(request.user).data
        data['duty'] = duty_payload(request.user)
        return Response(data)


class LogoutView(APIView):
    """Sign out: close the duty session and destroy the token.

    There was no way to sign out before -- the client simply dropped the
    token from localStorage and the server-side token lived on forever.
    That is now a correctness problem as well as a security one, because
    an operator's duty session has to be closed when they leave the line.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        closed = stop_duty(request.user)
        Token.objects.filter(user=request.user).delete()
        return Response(
            {'message': 'Signed out.', 'duty_sessions_closed': closed},
            status=status.HTTP_200_OK,
        )


# ============================================================
# User administration
# ============================================================

class UserListCreateView(generics.ListCreateAPIView):
    """List everybody, and add a person.

    Managers administer operators and shift incharges. Only the
    administrator can create another manager -- enforced in the serializer,
    so the rule holds for updates too.

    A new account is PENDING and has no password: the person chooses their
    own at first sign-in. Nobody ever sets a password for somebody else.
    """

    permission_classes = [IsManager]

    def get_queryset(self):
        # Removed employees are not part of the workforce any more, so they
        # do not belong in a list of people to administer. Their rows remain
        # in the database for history and for upcoming features.
        return User.objects.exclude(status=User.Status.RETIRED)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return UserCreateSerializer
        return UserAdminSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserAdminSerializer(user).data,
                        status=status.HTTP_201_CREATED)


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Read, edit, or remove a person.

    Removing is administrator-only and NEVER deletes the row. A removal
    retires the account: it can no longer sign in, but the record stays.

    Two reasons it is never a real delete. First,
    ChangeoverCorrectionRequest.requested_by is a CASCADE foreign key to this
    table, so deleting somebody who filed a correction would take that
    history with them. Second, user records are needed by upcoming features,
    so every account that has ever existed stays queryable.

    The consequence worth knowing: a retired row keeps its employee_id, and
    that column is unique -- so a removed ID cannot be issued to somebody
    else. Restore the account instead if the same person returns.
    """

    queryset = User.objects.all()
    lookup_field = 'employee_id'

    def get_permissions(self):
        # Editing is a manager job; removing somebody is not.
        if self.request.method == 'DELETE':
            return [IsAdmin()]
        return [IsManager()]

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return UserUpdateSerializer
        return UserAdminSerializer

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserAdminSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()

        if user == request.user:
            return Response(
                {'error': 'You cannot remove your own account.'},
                status=status.HTTP_400_BAD_REQUEST)
        if user.is_superuser:
            return Response(
                {'error': 'An administrator account cannot be removed here.'},
                status=status.HTTP_400_BAD_REQUEST)

        Token.objects.filter(user=user).delete()
        user.retire()
        return Response(
            {
                'action': 'retired',
                'message': f'{user.employee_id} has been removed and can no '
                           'longer sign in. This is permanent -- the record '
                           'is kept only so past work still shows who did '
                           'it.',
                'user': UserAdminSerializer(user).data,
            },
            status=status.HTTP_200_OK)


class UserResetPasswordView(APIView):
    """Clear somebody's password so they can set a new one.

    The forgotten-password route, for an employee who still works here.
    Rather than anyone issuing a temporary password, the account goes back to
    awaiting a password: the next sign-in attempt walks them through choosing
    one. Administrator only.

    This is NOT how an employee is removed -- see UserDetailView.destroy.
    """

    permission_classes = [IsAdmin]

    def post(self, request, employee_id):
        try:
            user = User.objects.get(employee_id=employee_id)
        except User.DoesNotExist:
            return Response({'error': 'No such employee ID.'},
                            status=status.HTTP_404_NOT_FOUND)

        if user.status == User.Status.RETIRED:
            return Response(
                {'error': 'That employee has been removed. Their password '
                          'cannot be reset.'},
                status=status.HTTP_400_BAD_REQUEST)

        if user.is_superuser and user != request.user:
            return Response(
                {'error': 'Another administrator account cannot be reset here.'},
                status=status.HTTP_400_BAD_REQUEST)

        user.status = User.Status.PENDING
        user.is_active = False
        user.set_unusable_password()
        user.save(update_fields=['status', 'is_active', 'password'])
        Token.objects.filter(user=user).delete()

        return Response({
            'message': f'{user.employee_id} can now set a new password at '
                       'sign-in.',
            'user': UserAdminSerializer(user).data,
        }, status=status.HTTP_200_OK)


class VerifyOwnPasswordView(APIView):
    """Step one of an administrator changing their own password.

    Nobody can reset the administrator -- there is no higher account to do
    it, and the reset endpoint refuses other administrators on purpose. So
    the administrator changes their own password in place, proving they know
    the current one first.
    """

    permission_classes = [IsAdmin]

    def post(self, request):
        serializer = VerifyPasswordSerializer(
            data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'verified': True}, status=status.HTTP_200_OK)


class ChangeOwnPasswordView(APIView):
    """Step two: set the new password.

    The current password is checked again here. Passing the verify step is
    not a ticket -- a client could skip straight to this endpoint, so it has
    to stand on its own.

    Every existing token is revoked and a fresh one issued, so a session
    opened with the old password cannot continue.
    """

    permission_classes = [IsAdmin]

    def post(self, request):
        serializer = ChangeOwnPasswordSerializer(
            data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)

        return Response({
            'message': 'Your password has been changed.',
            'token': token.key,
        }, status=status.HTTP_200_OK)
