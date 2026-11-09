"""Duty sessions, and rebuilding a changeover's operator attribution.

The arithmetic lives in setup_allocation.py, which knows nothing about the
database. This module is the part that talks to it: it reads duty sessions
and interruptions out of the database, hands them over as plain tuples, and
writes the result back.
"""

import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ChangeoverSummary,
    DutySession,
    LineStoppage,
    SetupAllocation,
    SetupAttribution,
)
from .setup_allocation import DutySpan, Pause, allocate_setup_time
from .shifts import shift_end_after, shift_for


def as_naive_local(value):
    """Coerce a timestamp to the naive local wall-clock time used here.

    Everything stored is naive -- USE_TZ is False and LocalTimeDateTimeField
    strips tzinfo on the way in. But a changeover handed over straight from
    the pipeline has not been through that field yet: its timestamps are
    still pandas Timestamps, sometimes timezone-aware, and comparing one of
    those against a naive shift boundary raises TypeError.

    Converting here rather than inside the allocation engine keeps the
    engine ignorant of pandas, which is what makes it testable on its own.
    """
    if value is None:
        return None

    # pandas Timestamp subclasses datetime but carries extra behaviour;
    # to_pydatetime gives a plain one.
    to_pydatetime = getattr(value, 'to_pydatetime', None)
    if callable(to_pydatetime):
        value = to_pydatetime()

    if timezone.is_aware(value):
        value = timezone.localtime(value).replace(tzinfo=None)
    return value


def _now():
    # USE_TZ is False and every stored column is naive local wall-clock
    # time, so this is the project's clock. Kept in one place so tests can
    # pass an explicit moment instead of patching it.
    return datetime.datetime.now()


# ------------------------------------------------------------------ duty

def is_line_operator(user):
    """Whether signing in puts this person on the line.

    Only operators. A shift incharge or manager signing in to look at the
    dashboard is not running the machine, and counting them would credit
    setup minutes to somebody who did not do the setup.
    """
    from user_authentication.models import User
    return getattr(user, 'role', None) == User.Role.OPERATOR


def _close(session, at, reason):
    """Close one session, never later than the shift it belongs to.

    A session cannot honestly run past its own boundary. If it is being
    closed after that -- a browser that slept through the shift change, a
    terminal nobody touched until the next morning -- the shift still
    ended when it ended, and the extra hours were not time the operator
    spent on the line. Recording them would hand that operator the next
    shift's setup time.

    So the end is clamped to the boundary, and the reason becomes
    SHIFT_ENDED: that is what actually happened, whatever triggered the
    close.
    """
    boundary = shift_end_after(session.started_at)
    if at >= boundary:
        at, reason = boundary, DutySession.EndReason.SHIFT_ENDED

    session.ended_at = at
    session.end_reason = reason
    session.save(update_fields=['ended_at', 'end_reason'])
    return session


@transaction.atomic
def open_duty_session(user, at=None):
    """Put an operator on the line. Returns the session, or None.

    Any session the same person left open is closed first. That happens
    when a browser is shut without signing out: the old session would
    otherwise stay open forever and keep collecting setup time the person
    was not there for.
    """
    if not is_line_operator(user):
        return None

    at = at or _now()
    shift, production_date = shift_for(at)

    for stale in DutySession.objects.filter(user=user, ended_at__isnull=True):
        _close(stale, at, DutySession.EndReason.SUPERSEDED)

    return DutySession.objects.create(
        user=user,
        started_at=at,
        shift=shift,
        production_date=production_date,
    )


@transaction.atomic
def close_duty_sessions(user, at=None,
                        reason=DutySession.EndReason.SIGNED_OUT):
    """Close whatever this person has open. Returns how many were closed."""
    at = at or _now()
    sessions = list(
        DutySession.objects.filter(user=user, ended_at__isnull=True))
    for session in sessions:
        _close(session, at, reason)
    return len(sessions)


def current_duty_session(user):
    return DutySession.objects.filter(
        user=user, ended_at__isnull=True).order_by('-started_at').first()


def expired_duty_sessions(now=None):
    """Open sessions whose shift has already ended.

    A session expires at the first shift boundary after it started, not
    eight hours later: an operator signing in at 14:50 is on A shift for
    ten minutes, and B shift starts without them.
    """
    now = now or _now()
    stale = []
    for session in DutySession.objects.filter(ended_at__isnull=True):
        if shift_end_after(session.started_at) <= now:
            stale.append(session)
    return stale


# ------------------------------------------------------- allocation rebuild

def _duty_spans_for(window_start, window_end):
    sessions = DutySession.objects.filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=window_start),
        started_at__lt=window_end,
    ).select_related('user')
    return [
        DutySpan(operator_key=session.user_id,
                 start=as_naive_local(session.started_at),
                 end=as_naive_local(session.ended_at))
        for session in sessions
    ]


def stoppages_overlapping(window_start, window_end, approved_only=False):
    """Stoppages that touch a window, open ones included.

    A stoppage belongs to no changeover -- it is a fact about the line on
    a timeline -- so the connection is made here, by overlap, the same
    way duty sessions are matched. The engine clips whatever it is given
    to the window, so a stoppage that only partly overlaps contributes
    only its overlapping part.
    """
    stoppages = LineStoppage.objects.filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=window_start),
        started_at__lt=window_end,
    ).select_related('reason', 'recorded_by', 'reviewed_by')

    if approved_only:
        # A pending stoppage is a claim nobody has agreed to yet.
        # Counting it would let the claim take effect before review.
        stoppages = stoppages.filter(status=LineStoppage.Status.APPROVED)
    return stoppages


def changeovers_overlapping(window_start, window_end):
    """Changeovers whose setup window a stoppage could affect.

    Used to work out what to recompute when a stoppage is recorded,
    closed or reviewed -- there is no foreign key to follow.
    """
    if window_start is None:
        return ChangeoverSummary.objects.none()
    end = window_end or window_start

    return ChangeoverSummary.objects.filter(
        setup_start_timestamp__isnull=False,
        setup_complete_timestamp__isnull=False,
        setup_start_timestamp__lt=end,
        setup_complete_timestamp__gt=window_start,
    )


def rebuild_for_stoppage(stoppage):
    """Recompute every changeover a stoppage could have changed.

    An open stoppage has no end, so it reaches forward indefinitely --
    treated as running to now, which is as far as any detected setup
    window can have got.
    """
    start = as_naive_local(stoppage.started_at)
    end = as_naive_local(stoppage.ended_at) or _now()
    rebuilt = 0
    for changeover in changeovers_overlapping(start, end):
        rebuild_allocations(changeover)
        rebuilt += 1
    return rebuilt


@transaction.atomic
def rebuild_allocations(changeover):
    """Recompute every allocation row for one changeover.

    Called after the pipeline writes a changeover and whenever an
    interruption on it is recorded or edited. Allocations are derived, so
    rebuilding is always safe: the previous rows are replaced outright
    rather than patched, which keeps them from drifting out of step with
    the duty sessions they came from.

    Returns the list of saved SetupAllocation rows.
    """
    start = as_naive_local(changeover.setup_start_timestamp)
    end = as_naive_local(changeover.setup_complete_timestamp)

    SetupAllocation.objects.filter(changeover=changeover).delete()

    if start is None or end is None or end <= start:
        # No usable setup window -- an in-flight changeover, or one the
        # signal never resolved. Nothing to attribute yet.
        return []

    pauses = [
        Pause(start=as_naive_local(item.started_at),
              end=as_naive_local(item.ended_at))
        for item in stoppages_overlapping(start, end, approved_only=True)
    ]

    segments = allocate_setup_time(
        start, end,
        duty_spans=_duty_spans_for(start, end),
        interruptions=pauses,
    )

    # Manual attributions are an input, read fresh on every rebuild, so
    # an assignment survives the pipeline recomputing this changeover.
    assigned = {
        as_naive_local(row.segment_start): row.operator_id
        for row in SetupAttribution.objects.filter(changeover=changeover)
    }

    rows = []
    for segment in segments:
        operator_id = segment.operator_key
        manual = False
        # Only where the duty sessions could not answer. An assignment is
        # for filling a gap, not for overruling who was demonstrably
        # signed in.
        if operator_id is None and segment.start in assigned:
            operator_id = assigned[segment.start]
            manual = True

        rows.append(SetupAllocation(
            changeover=changeover,
            operator_id=operator_id,
            shift=segment.shift,
            production_date=segment.production_date,
            segment_start=segment.start,
            segment_end=segment.end,
            gross_minutes=segment.gross_minutes,
            interrupted_minutes=segment.interrupted_minutes,
            allocated_minutes=segment.allocated_minutes,
            is_manual=manual,
        ))

    return SetupAllocation.objects.bulk_create(rows)


def rebuild_allocations_for_batches(batches):
    """Rebuild a set of changeovers by batch number."""
    rebuilt = 0
    for changeover in ChangeoverSummary.objects.filter(batch__in=list(batches)):
        rebuild_allocations(changeover)
        rebuilt += 1
    return rebuilt
