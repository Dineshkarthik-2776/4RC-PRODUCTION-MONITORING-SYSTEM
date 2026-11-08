"""The plant's shift calendar.

Three eight-hour shifts, named for how the plant refers to them:

    A   07:00 - 15:00
    B   15:00 - 23:00
    C   23:00 - 07:00   (crosses midnight)

C is the awkward one. The hours after midnight belong to the production
date of the *previous* calendar day, because the shift started the evening
before -- so 02:00 on the 9th is C shift of the 8th.

This module is the single definition of those boundaries. The processing
pipeline uses it to stamp each changeover, and the sign-out task uses it to
work out when an operator's shift is over; before this existed the hours
were written out twice and could drift apart.

Everything here is naive local wall-clock time, matching USE_TZ = False and
the LocalTimeDateTimeField columns. Asia/Kolkata has no daylight saving, so
there are no missing or repeated hours to reason about.
"""

import datetime

# (name, start hour, end hour). The last entry wraps past midnight.
SHIFT_HOURS = (
    ('A', 7, 15),
    ('B', 15, 23),
    ('C', 23, 7),
)

BOUNDARY_HOURS = (7, 15, 23)


def shift_for(dt):
    """The shift and production date a moment belongs to.

    Returns (shift_name, production_date).
    """
    hour = dt.hour
    if 7 <= hour < 15:
        return 'A', dt.date()
    if 15 <= hour < 23:
        return 'B', dt.date()
    # C shift. Before 07:00 the production date is still yesterday's,
    # because the shift began at 23:00 the night before.
    if hour < 7:
        return 'C', (dt - datetime.timedelta(days=1)).date()
    return 'C', dt.date()


def shift_start_of(dt):
    """The moment the shift containing `dt` began."""
    hour = dt.hour
    day = dt.date()
    if 7 <= hour < 15:
        start_hour = 7
    elif 15 <= hour < 23:
        start_hour = 15
    elif hour >= 23:
        start_hour = 23
    else:
        # Between midnight and 07:00: the shift started at 23:00 yesterday.
        day = day - datetime.timedelta(days=1)
        start_hour = 23
    return datetime.datetime.combine(day, datetime.time(hour=start_hour))


def shift_end_after(dt):
    """The next boundary strictly after `dt`.

    This is when the shift containing `dt` ends -- and therefore when an
    operator signed in at `dt` should be signed out.
    """
    for hour in BOUNDARY_HOURS:
        candidate = datetime.datetime.combine(dt.date(), datetime.time(hour=hour))
        if candidate > dt:
            return candidate
    # Past 23:00, so the next boundary is 07:00 tomorrow.
    return datetime.datetime.combine(
        dt.date() + datetime.timedelta(days=1), datetime.time(hour=7))


def boundaries_between(start, end):
    """Every shift boundary strictly inside (start, end), in order.

    Used to cut a setup window that spans a shift change into the pieces
    that belong to each shift.
    """
    if end <= start:
        return []

    found = []
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        for hour in BOUNDARY_HOURS:
            moment = datetime.datetime.combine(day, datetime.time(hour=hour))
            if start < moment < end:
                found.append(moment)
        day += datetime.timedelta(days=1)
    return found
