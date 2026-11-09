"""Role-based permissions.

Three roles, forming a privilege ladder:

    OPERATOR        the dashboard, summaries, charts and filters, and the
                    Excel, Kepware and PDF exports. Writes one thing: a
                    line stoppage, and only their own -- they are the
                    person at the machine when it stops.
    SHIFT_INCHARGE  the above, plus recording overshoot reasons: adding and
                    updating them inside the 5-hour window, and submitting a
                    correction request after it. Approves both queues, and
                    sees the operator-wise summary.
    MANAGER         the above, plus overriding the 5-hour window, approving
                    or rejecting correction requests, and maintaining the
                    Recipe Master and Standard Time Master.

An administrator is a MANAGER carrying is_staff/is_superuser, which unlocks
Django's /admin/. There is no separate admin role.

Most of what is readable is readable by all three roles, so plain
IsAuthenticated is still right for the dashboard, the exports and the
reports. Two places read differently, and have their own class here: the
operator-wise summary, which ranks operators and so is a supervisor's
view, and a stoppage somebody else recorded, which anyone may read but
only its author or a supervisor may change.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def _role_of(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return None
    return getattr(user, 'role', None)


class IsManager(BasePermission):
    """Manager only: masters, correction approval, the 5-hour override."""

    message = 'This action is restricted to managers.'

    def has_permission(self, request, view):
        from user_authentication.models import User
        return _role_of(request) == User.Role.MANAGER


class IsAdmin(BasePermission):
    """The administrator: a manager carrying Django's is_superuser flag.

    Reserved for the things that change who can get in -- creating manager
    accounts, and resetting a password by sending an account back to PENDING.
    """

    message = 'This action is restricted to the administrator.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        return bool(user and user.is_authenticated and user.is_superuser)


class IsManagerOrReadOnly(BasePermission):
    """Any signed-in user may read; only a manager may write.

    Used where a master is readable by everyone because the dashboard needs
    it -- the recipe list populates the filter dropdown -- but editable only
    by managers.
    """

    message = 'Only managers can change this.'

    def has_permission(self, request, view):
        from user_authentication.models import User
        role = _role_of(request)
        if role is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        return role == User.Role.MANAGER


class CanRecordOvershootReason(BasePermission):
    """Shift incharge or manager.

    Operators are read-only, so they are refused here. The 5-hour window is a
    separate question handled inside the view: this class answers "may you
    record a reason at all", not "may you still change this one".
    """

    message = 'Only shift incharges and managers can record overshoot reasons.'

    def has_permission(self, request, view):
        from user_authentication.models import User
        return _role_of(request) in (User.Role.MANAGER, User.Role.SHIFT_INCHARGE)


# Kept under their previous names so existing imports keep working.
IsManagerUser = IsManager


class CanRecordSetupEvent(BasePermission):
    """Anybody on the floor may record what happened during a setup.

    Operators are read-only everywhere else, but not here. They are the
    people standing at the machine when the compound runs out, and an
    interruption nobody can record is an interruption that silently
    inflates their own setup time. Shift incharges and managers can record
    one too, for the terminal nobody was signed in to.
    """

    message = 'You must be signed in to record a setup event.'

    def has_permission(self, request, view):
        from user_authentication.models import User
        return _role_of(request) in (
            User.Role.OPERATOR,
            User.Role.SHIFT_INCHARGE,
            User.Role.MANAGER,
        )


class CanAmendSetupEvent(BasePermission):
    """Who may change or withdraw a stoppage somebody already recorded.

    Recording one is open to the whole floor (CanRecordSetupEvent).
    Changing one is not: a stoppage subtracts minutes from an
    operator's setup figure, so letting any operator edit or delete
    anybody's entry would let one person quietly rewrite another's
    numbers.

        manager, shift incharge  -- any stoppage. They review them.
        operator                 -- only the one they recorded.

    Reading is left open: the floor needs to see the stoppage that is
    running, whoever reported it.
    """

    message = ('You can only change an interruption you recorded. Ask a '
               'shift incharge or manager to change this one.')

    def has_permission(self, request, view):
        from user_authentication.models import User
        return _role_of(request) in (
            User.Role.OPERATOR,
            User.Role.SHIFT_INCHARGE,
            User.Role.MANAGER,
        )

    def has_object_permission(self, request, view, obj):
        from user_authentication.models import User
        if request.method in SAFE_METHODS:
            return True
        if _role_of(request) in (User.Role.MANAGER, User.Role.SHIFT_INCHARGE):
            return True
        # An operator, and only their own. LineStoppage.recorded_by is
        # NOT NULL, so there is always somebody to compare against.
        return obj.recorded_by_id == request.user.id


class CanApproveRequests(BasePermission):
    """Shift incharges and managers: the people who sign things off.

    Covers both approval queues -- correction requests against an
    overshoot reason, and recorded setup interruptions. They are the same
    kind of decision ("is this claim about what happened on the line
    true?") made by the same people, so they share one rule rather than
    drifting apart.

    Operators are refused. They raise these requests; somebody above them
    on the shift decides.
    """

    message = 'Only shift incharges and managers can approve requests.'

    def has_permission(self, request, view):
        from user_authentication.models import User
        return _role_of(request) in (
            User.Role.MANAGER,
            User.Role.SHIFT_INCHARGE,
        )
