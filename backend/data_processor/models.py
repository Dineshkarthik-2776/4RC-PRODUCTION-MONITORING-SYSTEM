from django.db import models
from django.utils import timezone


class LocalTimeDateTimeField(models.DateTimeField):
    def get_prep_value(self, value):
        return value

    def get_db_prep_value(self, value, connection, prepared=False):
        if value is None:
            return None

        if timezone.is_aware(value):
            value = timezone.localtime(value)
            value = value.replace(tzinfo=None)
        return super().get_db_prep_value(value, connection, prepared)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        # Keep the stored local wall-clock time as a naive datetime.
        # Re-attaching a timezone here causes the value to appear as
        # timezone-aware and can surface as +00:00 in the DB/client.
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.replace(tzinfo=None)

# Create your models here.
class RawLineData(models.Model) :
    """
    This model is associated with the 4RC table where actual Line Data read by sensors are stored.
    """
    id = models.BigAutoField(primary_key=True, db_column='id')
    machine_id = models.CharField(max_length=8, db_column='MachineID', null=True, blank=True)
    recipe_code = models.CharField(max_length=50, db_column='RecipeCode', null=True, blank=True)
    line_speed_actual = models.FloatField(db_column='LineSpeedActual', null=True, blank=True)
    line_speed_set = models.FloatField(db_column='LineSpeedSet', null=True, blank=True)
    timestamp = models.DateTimeField(db_column='TimeStamp')
    date_created = models.DateTimeField(db_column='DateCreated')
    processed_flag = models.BooleanField(default=False, db_column='ProcessedFlag')
    status = models.CharField(max_length=50, null=True, blank=True, db_column='Status')
    class Meta:
        managed = False
        db_table = 'FRC'
        ordering = ['timestamp']


class MaterialMaster(models.Model):
    """
    Maps to the 'bom' table (external BOM/ERP data).
    Filtered in the sync task to Equipment = '4RC'.
    """
    table_id = models.IntegerField(primary_key=True, db_column='TableID')
    recipe_code = models.TextField(db_column='FormulaCode', null=True, blank=True)
    sap_code = models.TextField(db_column='PLCBOMName', null=True, blank=True)
    equipment = models.TextField(db_column='Equipment', null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'bom'


class RecipeMaster(models.Model):
    """
       This model stores the target speed and recipe type
    """
    recipe_code = models.CharField(max_length=100, unique=True, primary_key=True)
    sap_code = models.CharField(max_length=100, null=True, blank=True)
    target_speed = models.FloatField()
    recipe_type = models.CharField(max_length=50, help_text="e.g., 'Steel' or 'Fabric'")

    class Meta:
        db_table = 'RecipeMaster'

class OvershootReasons( models.Model ) :
    category = models.CharField(max_length = 50 , null = True )
    reason = models.CharField(max_length= 100 , null = True )

class ChangeoverSummary(models.Model):
    """
    Processed Data which contains important statistics are stored in this Model
    """

    batch = models.FloatField(unique=True)
    recipe_change_time = LocalTimeDateTimeField(null=True, blank=True)
    current_recipe = models.TextField(null=True, blank=True)
    previous_recipe = models.TextField(null=True, blank=True)
    ramp_down_timestamp = LocalTimeDateTimeField(db_column='ramp_down', null=True, blank=True)
    setup_start_timestamp = LocalTimeDateTimeField(db_column='setup_start', null=True, blank=True)
    ramp_up_timestamp = LocalTimeDateTimeField(db_column='ramp_up', null=True, blank=True)
    setup_complete_timestamp = LocalTimeDateTimeField(db_column='setup_complete', null=True, blank=True)

    # --- Calculated Values ---
    ramp_up_time_loss = models.FloatField(null=True, blank=True)
    ramp_down_time_loss = models.FloatField(null=True, blank=True)

    # --- Values for Frontend ---
    setup_time_actual = models.FloatField(db_column='setup_time_act', null=True, blank=True)
    standard_time = models.BigIntegerField(null=True, blank=True)

    # --- CRITICAL NEW FIELDS (for 'static' and 'ramp') ---
    static_setup_time = models.FloatField(null=True, blank=True)
    ramp_up_time = models.FloatField(null=True, blank=True)

    # --- Classification ---
    current_type = models.TextField(null=True, blank=True)
    previous_type = models.TextField(null=True, blank=True)
    change_over_type = models.TextField(db_column='change_over', null=True, blank=True)

    overshoot = models.ForeignKey(OvershootReasons,on_delete=models.SET_NULL,null=True)

    remarks = models.TextField(null=True, blank=True)
    production_date = models.DateField(null=True, blank=True)
    shift = models.CharField(max_length=1, null=True, blank=True)

    class Meta:
        db_table = 'ChangeoverSummary'
        ordering = ['-recipe_change_time']


class ChangeoverCorrectionRequest(models.Model):
    """
    Stores requests from workers to update a ChangeoverSummary reason/category
    when the 5-hour window has passed.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    changeover = models.ForeignKey(ChangeoverSummary, on_delete=models.CASCADE, related_name='correction_requests')
    requested_by = models.ForeignKey('user_authentication.User', on_delete=models.CASCADE)
    
    # Store old values (snapshot)
    old_overshoot = models.ForeignKey(OvershootReasons, related_name='old_correction_requests', null=True, blank=True, on_delete=models.SET_NULL)
    
    # Store new requested values
    new_overshoot = models.ForeignKey(OvershootReasons, related_name='new_correction_requests', on_delete=models.CASCADE, null=True, blank=True)
    
    status = models.CharField(
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING
    )
    
    created_at = LocalTimeDateTimeField(auto_now_add=True)
    updated_at = LocalTimeDateTimeField(auto_now=True)

    class Meta:
        db_table = 'ChangeoverCorrectionRequest'
        ordering = ['-created_at']


class StandardTimeMaster(models.Model):
    """
    This model stores standard setup times for different changeover types
    """
    changeover_key = models.CharField(max_length=100, unique=True, help_text="e.g., 'Steel to Steel'")
    standard_time = models.FloatField()
    is_custom = models.BooleanField(default=False)

    class Meta:
        db_table = 'StandardTimeMaster'

class ChangeoverRule(models.Model):
    """
    Maps specific recipe transitions to a StandardTimeMaster.
    """
    standard_time_master = models.ForeignKey(
        StandardTimeMaster, 
        on_delete=models.CASCADE, 
        related_name='rules'
    )
    from_recipe = models.CharField(max_length=100)
    to_recipe = models.CharField(max_length=100)

    class Meta:
        db_table = 'ChangeoverRule'
        unique_together = ('from_recipe', 'to_recipe')


# ======================================================================
# Operator-wise setup capture
# ======================================================================

class DutySession(models.Model):
    """Who was on the line, and between which moments.

    The system had no idea who was standing at the machine at any given
    time, which is the one thing operator-wise setup time needs. Rather
    than asking operators to clock in to a second place they would forget,
    a duty session is their sign-in: it opens when an operator logs in and
    closes when they log out or when their shift ends.

    Only operators get one. A shift incharge or manager signing in to
    review the dashboard is not standing at the line, and counting their
    session would attribute setup minutes to somebody who did not do the
    setup.

    There is no machine column here. The raw sensor table has one
    (FRC.MachineID, currently all '4RC_M1'), but nothing downstream
    carries it -- not even ChangeoverSummary -- so a machine field here
    would be a third unused one holding a value nothing compares against.
    When a second line arrives the column goes on every model at once,
    where it can be tested.

    The user reference is PROTECT, not CASCADE: an employee's duty history
    is the evidence behind their setup figures, so the database refuses to
    delete a user out from under it. Removing an employee retires them
    instead, which leaves this intact.
    """

    class EndReason(models.TextChoices):
        SIGNED_OUT = 'signed_out', 'Signed out'
        SHIFT_ENDED = 'shift_ended', 'Shift ended'
        SUPERSEDED = 'superseded', 'Replaced by a newer sign-in'

    user = models.ForeignKey(
        'user_authentication.User', on_delete=models.PROTECT,
        related_name='duty_sessions')

    started_at = LocalTimeDateTimeField()
    ended_at = LocalTimeDateTimeField(null=True, blank=True)
    end_reason = models.CharField(
        max_length=20, choices=EndReason.choices, null=True, blank=True)

    # Stamped from the start time so the session can be found by shift
    # without recomputing it. A session that runs past its boundary is
    # closed by the sign-out task rather than being re-stamped.
    shift = models.CharField(max_length=1)
    production_date = models.DateField()

    class Meta:
        db_table = 'DutySession'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['production_date', 'shift']),
            models.Index(fields=['user', '-started_at']),
        ]

    def __str__(self):
        closed = self.ended_at.strftime('%H:%M') if self.ended_at else 'open'
        return f"{self.user.employee_id} {self.shift} shift " \
               f"{self.started_at:%Y-%m-%d %H:%M}-{closed}"

    @property
    def is_open(self):
        return self.ended_at is None


class LineStoppage(models.Model):
    """A stretch when the line was stopped for something other than setup.

    Recorded while it is happening, not afterwards. That is the whole
    point of this model having no changeover: a changeover row does not
    exist until the pipeline has resolved a *completed* setup window, and
    by then the operator who watched the line stop may well have been
    signed out at a shift boundary. Making them wait meant the one person
    who knew the times was the one person who could not record them.

    So a stoppage is a fact about the line on a timeline, like a duty
    session. When a setup window is later detected, whichever stoppages
    overlap it are clipped to it and subtracted -- the allocation engine
    already clips pauses to segments, so nothing new happens there.

    A stoppage with no end is still running; it counts up to the end of
    whatever setup window it lands in until somebody closes it.

    Only an approved stoppage changes anybody's figure. It subtracts
    minutes from an operator's setup time, which makes it a claim about
    that operator's performance, and it goes to the same people who
    approve an overshoot reason.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Awaiting approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    reason = models.ForeignKey(
        OvershootReasons, on_delete=models.PROTECT,
        related_name='line_stoppages',
        help_text="From the shared reason master, as the overshoot "
                  "dropdown uses.")
    note = models.TextField(
        blank=True, default='',
        help_text="Required when the chosen reason is an 'Other' one.")

    started_at = LocalTimeDateTimeField()
    ended_at = LocalTimeDateTimeField(
        null=True, blank=True,
        help_text="Null while the line is still stopped.")

    recorded_by = models.ForeignKey(
        'user_authentication.User', on_delete=models.PROTECT,
        related_name='line_stoppages')
    created_at = LocalTimeDateTimeField(auto_now_add=True)
    updated_at = LocalTimeDateTimeField(auto_now=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        'user_authentication.User', on_delete=models.PROTECT,
        null=True, blank=True, related_name='reviewed_stoppages')
    reviewed_at = LocalTimeDateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default='')

    # What happened to this stoppage at a shift boundary.
    #
    # An open stoppage is closed automatically when its shift ends, for
    # the same reason a duty session is: left running it would keep
    # matching every later setup window, and once approved it would
    # remove the setup time from every changeover after it. There is no
    # bound on that damage, so the stoppage is closed at the boundary and
    # the incoming shift is asked whether the line is still down.
    #
    # CONTINUED creates a fresh stoppage starting at the boundary, so no
    # minutes are lost and each shift owns its own record.
    boundary_handover = models.CharField(
        max_length=10, blank=True, default='',
        choices=[
            ('pending', 'Waiting on the next shift'),
            ('continued', 'Line was still down'),
            ('ended', 'Line had already resumed'),
        ])

    class Meta:
        db_table = 'LineStoppage'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['started_at', 'ended_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        closed = self.ended_at.strftime('%H:%M') if self.ended_at else 'ongoing'
        return (f"{self.reason.reason} "
                f"{self.started_at:%Y-%m-%d %H:%M}-{closed}")

    @property
    def is_running(self):
        return self.ended_at is None

    @property
    def counts(self):
        """Whether this stoppage actually reduces anybody's setup time."""
        return self.status == self.Status.APPROVED

    @property
    def duration_minutes(self):
        if self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at).total_seconds() / 60.0, 2)

    @property
    def needs_note(self):
        """Whether this reason is too vague to stand without an explanation.

        Keyed on the master saying "Other", which is the convention its
        rows already follow. If a plant renames that row the requirement
        quietly stops applying -- worth knowing, but better than a second
        hard-coded list that would be wrong in a louder way.
        """
        return 'other' in (f"{self.reason.category} {self.reason.reason}"
                           .lower())


class SetupAttribution(models.Model):
    """Somebody saying who worked a piece of setup the system could not tell.

    Setup happens with nobody signed in -- a terminal nobody touched, a
    sign-in forgotten -- and those minutes come out unattributed. A shift
    incharge or manager knows who was actually there and says so here.

    This is an INPUT to the allocation, deliberately, not an edit of the
    result. SetupAllocation rows are deleted and recomputed on every
    pipeline pass, so an assignment written onto one would survive about
    five minutes. Stored here it is re-read on every rebuild, exactly like
    a duty session or an interruption.

    Keyed on the segment's start rather than the allocation row's id,
    because that id does not survive a rebuild either. If the detected
    setup window later moves, the segment boundary moves with it and the
    assignment no longer matches -- the time returns to unattributed
    rather than being silently applied to the wrong stretch.
    """

    changeover = models.ForeignKey(
        ChangeoverSummary, on_delete=models.CASCADE,
        related_name='attributions')
    segment_start = LocalTimeDateTimeField()

    operator = models.ForeignKey(
        'user_authentication.User', on_delete=models.PROTECT,
        related_name='assigned_setup_segments')
    assigned_by = models.ForeignKey(
        'user_authentication.User', on_delete=models.PROTECT,
        related_name='setup_attributions_made')
    assigned_at = LocalTimeDateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'SetupAttribution'
        ordering = ['segment_start']
        constraints = [
            models.UniqueConstraint(
                fields=['changeover', 'segment_start'],
                name='one_attribution_per_segment'),
        ]

    def __str__(self):
        return (f"{self.operator.employee_id} on batch "
                f"{self.changeover_id} from {self.segment_start:%H:%M}")


class SetupAllocation(models.Model):
    """One piece of a setup window, credited to one operator in one shift.

    Derived, never typed: the pipeline recomputes every allocation for a
    changeover whenever its setup window or its interruptions change, so
    these rows can always be rebuilt from the duty sessions and the
    interruptions. Nothing should edit them directly.

    `operator` is nullable on purpose. Setup work happens with nobody
    signed in, and those minutes still have to appear somewhere -- showing
    them as unattributed is the honest answer, and it makes the gap
    visible rather than quietly dropping the time.
    """

    changeover = models.ForeignKey(
        ChangeoverSummary, on_delete=models.CASCADE,
        related_name='allocations')
    operator = models.ForeignKey(
        'user_authentication.User', on_delete=models.PROTECT,
        null=True, blank=True, related_name='setup_allocations')

    shift = models.CharField(max_length=1)
    production_date = models.DateField()

    segment_start = LocalTimeDateTimeField()
    segment_end = LocalTimeDateTimeField()

    gross_minutes = models.FloatField()
    interrupted_minutes = models.FloatField(default=0.0)
    allocated_minutes = models.FloatField()

    # True when a person assigned this segment rather than the duty
    # sessions deciding it. Worth showing: a figure somebody typed in
    # deserves to be read differently from one the line produced.
    is_manual = models.BooleanField(default=False)

    class Meta:
        db_table = 'SetupAllocation'
        ordering = ['segment_start']
        constraints = [
            models.UniqueConstraint(
                fields=['changeover', 'segment_start'],
                name='one_allocation_per_segment'),
        ]
        indexes = [
            models.Index(fields=['production_date', 'shift']),
            models.Index(fields=['operator', 'production_date']),
        ]

    def __str__(self):
        who = self.operator.employee_id if self.operator else 'unattributed'
        return f"{who} {self.allocated_minutes:.1f} min " \
               f"({self.shift} shift {self.production_date})"
