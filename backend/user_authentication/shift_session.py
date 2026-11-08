"""The authentication side of being on duty.

Signing in is what puts an operator on the line, so the duty record is
created here rather than in a separate screen nobody would remember to
use. The bookkeeping itself lives in data_processor.setup_capture, next to
the allocation that consumes it; this module is the adapter, and the one
place that decides what the client is told about its own shift.

Imports are deferred into the functions on purpose: data_processor imports
the user model, so importing it at module level here would close the loop.
"""

import datetime


def start_duty(user):
    """Open a duty session if this person is a line operator, else None."""
    from data_processor.setup_capture import open_duty_session
    return open_duty_session(user)


def stop_duty(user):
    """Close any open duty session. Returns how many were closed."""
    from data_processor.setup_capture import close_duty_sessions
    return close_duty_sessions(user)


def duty_payload(user, session=None):
    """What the browser needs to manage its own shift.

    `shift_ends_at` is the contract that matters: the client warns before
    it and signs itself out at it. The server enforces the same boundary
    from the Celery task, so a client that ignores this is still cut off --
    this just makes the sign-out graceful instead of a surprise 401.

    Returns None for anyone who is not on duty, which the client reads as
    "this session is not tied to a shift": shift incharges, managers and
    the administrator stay signed in across boundaries.
    """
    from data_processor.setup_capture import current_duty_session, is_line_operator
    from data_processor.shifts import shift_end_after

    if not is_line_operator(user):
        return None

    session = session or current_duty_session(user)
    if session is None:
        return None

    ends_at = shift_end_after(session.started_at)
    now = datetime.datetime.now()

    return {
        'session_id': session.id,
        'shift': session.shift,
        'production_date': session.production_date.isoformat(),
        'started_at': session.started_at.isoformat(),
        'shift_ends_at': ends_at.isoformat(),
        'seconds_remaining': max(int((ends_at - now).total_seconds()), 0),
    }
