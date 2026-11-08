"""Splitting a setup window across the operators and shifts that worked it.

A changeover's setup window is detected from the line-speed signal: one
start, one end. That single span has to be divided before it can be
reported "operator-wise", because during those minutes the shift can turn
over, the operator can hand over, and the line can stop for a breakdown
that is nobody's setup time at all.

The rule is the same in every case: cut the window wherever something
changed, attribute each piece to whoever was on the line for it, and
subtract any interruption overlapping that piece.

That single rule covers the five scenarios in the process flow:

    1  same shift, same operator      no cuts; one segment
    2  setup crosses a shift change   cut at the boundary; same operator
                                      appears twice, under two shifts
    3  operator changes mid-shift     cut at the handover
    4  both change                    cut at both
    5  interrupted setup              segments are unchanged; the paused
                                      minutes are subtracted from whichever
                                      segments they overlap

Everything here is a pure function over naive local datetimes -- no models,
no database -- so the arithmetic can be tested directly, which is where the
scenarios actually live.
"""

import datetime
from collections import namedtuple

from .shifts import boundaries_between, shift_for

# Who was on the line, and when. `operator_key` is whatever the caller uses
# to identify a person; the engine only compares it.
DutySpan = namedtuple('DutySpan', 'operator_key start end')

# A stretch the setup timer should not count.
Pause = namedtuple('Pause', 'start end')

Segment = namedtuple(
    'Segment',
    'operator_key shift production_date start end '
    'gross_minutes interrupted_minutes allocated_minutes',
)


def _minutes(start, end):
    return max((end - start).total_seconds() / 60.0, 0.0)


def _overlap_minutes(a_start, a_end, b_start, b_end):
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return _minutes(start, end)


def _operator_at(duty_spans, moment):
    """Who was on the line at `moment`.

    Returns None when nobody was signed in -- which is a real answer, not a
    failure. Setup work happens at 3am with nobody at the terminal, and the
    time still has to be accounted for somewhere.

    If two spans overlap, the later sign-in wins. That should not happen on
    a single terminal, but a forgotten sign-out on a second device would
    produce it, and the newer session is the better guess at who is
    actually standing there.
    """
    covering = [
        span for span in duty_spans
        if span.start <= moment and (span.end is None or moment < span.end)
    ]
    if not covering:
        return None
    return max(covering, key=lambda span: span.start).operator_key


def _cut_points(setup_start, setup_end, duty_spans):
    """Every moment inside the window where attribution could change."""
    points = {setup_start, setup_end}
    points.update(boundaries_between(setup_start, setup_end))

    for span in duty_spans:
        for edge in (span.start, span.end):
            if edge is not None and setup_start < edge < setup_end:
                points.add(edge)

    return sorted(points)


def _merge(segments):
    """Join neighbouring pieces that belong to the same person and shift.

    A cut point only matters if something actually changed across it. An
    operator who signs out and straight back in during one setup produced
    two segments that are really one stretch of their time, and reporting
    them separately would overstate how fragmented the setup was.
    """
    merged = []
    for segment in segments:
        previous = merged[-1] if merged else None
        same_run = (
            previous is not None
            and previous.operator_key == segment.operator_key
            and previous.shift == segment.shift
            and previous.production_date == segment.production_date
            and previous.end == segment.start
        )
        if same_run:
            merged[-1] = previous._replace(
                end=segment.end,
                gross_minutes=previous.gross_minutes + segment.gross_minutes,
                interrupted_minutes=(
                    previous.interrupted_minutes + segment.interrupted_minutes),
                allocated_minutes=(
                    previous.allocated_minutes + segment.allocated_minutes),
            )
        else:
            merged.append(segment)
    return merged


def allocate_setup_time(setup_start, setup_end, duty_spans=(), interruptions=()):
    """Divide one setup window into attributed segments.

    `duty_spans`    DutySpan entries; `end` may be None for a session still
                    open.
    `interruptions` Pause entries; `end` may be None for one still running,
                    which is treated as lasting to the end of the setup.

    Returns a list of Segment in time order. The allocated minutes across
    all segments sum to the setup window less the interrupted minutes.
    """
    if setup_start is None or setup_end is None or setup_end <= setup_start:
        return []

    pauses = [
        Pause(pause.start, pause.end if pause.end is not None else setup_end)
        for pause in interruptions
        if pause.start is not None
    ]

    segments = []
    points = _cut_points(setup_start, setup_end, duty_spans)

    for start, end in zip(points, points[1:]):
        if end <= start:
            continue

        # The midpoint, not the start: a segment beginning exactly at a
        # handover would otherwise be credited to the operator leaving.
        midpoint = start + (end - start) / 2
        shift, production_date = shift_for(start)

        gross = _minutes(start, end)
        interrupted = sum(
            _overlap_minutes(start, end, pause.start, pause.end)
            for pause in pauses
        )
        # Overlapping interruptions could otherwise subtract more than the
        # segment is long.
        interrupted = min(interrupted, gross)

        segments.append(Segment(
            operator_key=_operator_at(duty_spans, midpoint),
            shift=shift,
            production_date=production_date,
            start=start,
            end=end,
            gross_minutes=round(gross, 2),
            interrupted_minutes=round(interrupted, 2),
            allocated_minutes=round(gross - interrupted, 2),
        ))

    return _merge(segments)
