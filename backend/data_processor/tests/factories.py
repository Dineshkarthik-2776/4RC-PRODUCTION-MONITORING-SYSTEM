"""Builders for pipeline test data.

The detection algorithm reads a line-speed signal, so integration tests need
realistic sample runs rather than single rows. build_changeover() emits the
shape the algorithm looks for (documentation section 3.3):

    steady run on the outgoing recipe
      -> ramp down to 0        (Point A, then B when it reaches 0)
      -> stopped for setup
      -> ramp up               (Point C)
      -> steady at target      (Point D)

Timestamps are generated backwards from now because the task only considers
rows from the last three days.
"""

import datetime

from data_processor.models import (
    ChangeoverRule,
    RawLineData,
    RecipeMaster,
    StandardTimeMaster,
)

SAMPLE_SECONDS = 10
MACHINE_ID = '4RC_M1'


def make_recipe(code, target_speed=20.0, recipe_type='fabric', sap_code=None):
    return RecipeMaster.objects.create(
        recipe_code=code,
        target_speed=target_speed,
        recipe_type=recipe_type,
        sap_code=sap_code or f'SAP-{code.replace(" ", "")}',
    )


def make_standard_time(key, minutes=10.0, is_custom=False):
    return StandardTimeMaster.objects.create(
        changeover_key=key, standard_time=minutes, is_custom=is_custom,
    )


def make_rule(standard_time_master, from_recipe, to_recipe):
    return ChangeoverRule.objects.create(
        standard_time_master=standard_time_master,
        from_recipe=from_recipe,
        to_recipe=to_recipe,
    )


def write_samples(rows, start_at, processed=False):
    """Persist (recipe_code, speed) pairs as consecutive RawLineData rows."""
    created = []
    for offset, (recipe_code, speed) in enumerate(rows):
        stamp = start_at + datetime.timedelta(seconds=offset * SAMPLE_SECONDS)
        created.append(RawLineData(
            machine_id=MACHINE_ID,
            recipe_code=recipe_code,
            line_speed_actual=float(speed),
            line_speed_set=float(speed),
            timestamp=stamp,
            date_created=stamp,
            processed_flag=processed,
        ))
    RawLineData.objects.bulk_create(created)
    return created


def changeover_samples(
    previous_recipe='RECIPE A',
    current_recipe='RECIPE B',
    target_speed=20.0,
    steady_samples=20,
    rampdown_samples=6,
    stopped_samples=30,
    rampup_samples=20,
):
    """Return the (recipe_code, speed) sequence for one complete changeover."""
    rows = []

    # Outgoing recipe at target speed.
    rows += [(previous_recipe, target_speed)] * steady_samples

    # Decelerating to a standstill.
    for i in range(rampdown_samples):
        remaining = target_speed * (rampdown_samples - 1 - i) / (rampdown_samples - 1)
        rows.append((previous_recipe, round(remaining, 2)))

    # Stopped: this is the setup work. The recipe code switches partway
    # through the stop, which is what actually happens on the line -- the
    # operator changes the setting while the machine is at a standstill.
    #
    # It also matters to the algorithm: the remarks check requires a
    # zero-speed sample in BOTH the outgoing and the incoming batch
    # ("zero speed was not reached during the setup from/for ..."), so a stop
    # attributed entirely to one recipe reads as an unmeasurable changeover.
    half = stopped_samples // 2
    rows += [(previous_recipe, 0.0)] * half
    rows += [(current_recipe, 0.0)] * (stopped_samples - half)

    # Incoming recipe accelerating from rest.
    for i in range(rampup_samples):
        speed = target_speed * (i + 1) / rampup_samples
        rows.append((current_recipe, round(speed, 2)))

    # Settled at target.
    rows += [(current_recipe, target_speed)] * steady_samples

    return rows


def build_changeover(hours_ago=6, **kwargs):
    """Create masters-free raw data for one changeover, ending `hours_ago`.

    Caller is responsible for the RecipeMaster rows, so tests can exercise the
    missing-recipe paths by simply not creating them.
    """
    rows = changeover_samples(**kwargs)
    span = datetime.timedelta(seconds=len(rows) * SAMPLE_SECONDS)
    start_at = datetime.datetime.now() - datetime.timedelta(hours=hours_ago) - span
    return write_samples(rows, start_at)
