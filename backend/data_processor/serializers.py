import datetime

from rest_framework import serializers
from django.utils import timezone
from .models import (
    ChangeoverSummary, RecipeMaster, ChangeoverCorrectionRequest,
    StandardTimeMaster, OvershootReasons, ChangeoverRule,
    DutySession, LineStoppage, SetupAllocation, SetupAttribution,
)


def _format_local_timestamp(value):
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    value = timezone.localtime(value)
    return value.replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')


class ChangeoverRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangeoverRule
        fields = '__all__'
        read_only_fields = ['standard_time_master']

class StandardTimeSerializer(serializers.ModelSerializer):
    rules = ChangeoverRuleSerializer(many=True, read_only=True)
    
    class Meta:
        model = StandardTimeMaster
        fields = '__all__'
        read_only_fields = ['changeover_key', 'is_custom']

class OvershootReasonsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OvershootReasons
        fields = '__all__'


class RecipeMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeMaster
        fields = '__all__'
        read_only_fields = ['recipe_code', 'sap_code']


class RecipeMasterCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeMaster
        fields = ['recipe_code', 'sap_code', 'recipe_type', 'target_speed']

    def validate_target_speed(self, value):
        if value is None or value < 1:
            raise serializers.ValidationError("target_speed must be 1 or greater.")
        return value




class ChangeoverDetailSerializer(serializers.ModelSerializer):
    """
    Serializes a single changeover event.
    This is for the "details" array in your sampleData.
    """
    # 1. We're RENAMING fields from our database (like 'current_recipe')
    #    to match your frontend's JSON (like 'material').
    current_recipe = serializers.CharField()
    from_recipe = serializers.CharField(source='previous_recipe')
    to_recipe = serializers.CharField(source='current_recipe')
    Std = serializers.FloatField(source='standard_time')
    act = serializers.FloatField(source='setup_time_actual')
    static = serializers.FloatField(source='static_setup_time')
    ramp = serializers.FloatField(source='ramp_up_time')
    start_time = serializers.SerializerMethodField()
    setup_start_time = serializers.SerializerMethodField()
    ramp_up_time = serializers.SerializerMethodField()
    setup_complete_time = serializers.SerializerMethodField()
    overshoot_category = serializers.CharField(source='overshoot.category', read_only=True)
    overshoot_reason = serializers.CharField(source='overshoot.reason', read_only=True)
    overshoot = serializers.PrimaryKeyRelatedField(queryset=OvershootReasons.objects.all(), write_only=True, required=False, allow_null=True)
    # The same id, readable. `overshoot` is write_only, so a saved reason
    # never came back to the client -- the dropdown had no value to
    # select and fell back to "Select Reason" every time the panel
    # reopened, even though the reason was stored. The category showed
    # correctly because it is a separate read-only field, which is what
    # made the bug look like the reason alone had not saved.
    overshoot_id = serializers.PrimaryKeyRelatedField(
        source='overshoot', read_only=True)

    # 2. This creates a NEW field called 'shoot' that doesn't exist
    #    in our database.
    shoot = serializers.SerializerMethodField()

    class Meta:
        model = ChangeoverSummary
        fields = [
            'id',
            'current_recipe',
            'from_recipe',
            'to_recipe',
            'start_time',
            'setup_start_time',
            'ramp_up_time',
            'setup_complete_time',
            'Std',
            'act',
            'static',
            'ramp',
            'shoot',
            'overshoot',
            'overshoot_id',
            'overshoot_category',
            'overshoot_reason',
            'remarks',
            'production_date',
            'shift'
        ]

    def get_shoot(self, obj):
        """
        Calculates Shoot = Actual Time - Standard Time
        """
        if obj.setup_time_actual is not None and obj.standard_time is not None:
            shoot_value = obj.setup_time_actual - obj.standard_time
            return round(shoot_value, 2)
        return None

    def get_start_time(self, obj):
        return _format_local_timestamp(obj.recipe_change_time)

    def get_setup_start_time(self, obj):
        return _format_local_timestamp(obj.setup_start_timestamp)

    def get_ramp_up_time(self, obj):
        return _format_local_timestamp(obj.ramp_up_timestamp)

    def get_setup_complete_time(self, obj):
        return _format_local_timestamp(obj.setup_complete_timestamp)

    def to_representation(self, instance):
        representation = super().to_representation(instance)

        # Map null stat values to "NA"
        stats_fields = ['Std', 'act', 'static', 'ramp', 'shoot']
        for field in stats_fields:
            if representation.get(field) is None:
                representation[field] = "N/A"
                
        return representation

class ChangeoverUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for a worker to update the reason and category for an overshoot.
    """
    class Meta:
        model = ChangeoverSummary
        # These are the only two fields a worker is allowed to change
        fields = [
            'overshoot'
        ]

class ChangeoverCorrectionRequestSerializer(serializers.ModelSerializer):
    """
    Serializer to display correction requests to managers.
    """
    changeover_id = serializers.PrimaryKeyRelatedField(read_only=True, source='changeover.id')
    # Who asked, as a manager would recognise them: '10433 - S Raman'.
    requested_by_label = serializers.SerializerMethodField()
    current_recipe = serializers.CharField(read_only=True, source='changeover.current_recipe')
    
    old_category = serializers.CharField(read_only=True, source='old_overshoot.category', default=None)
    old_reason = serializers.CharField(read_only=True, source='old_overshoot.reason', default=None)
    new_category = serializers.CharField(read_only=True, source='new_overshoot.category', default=None)
    new_reason = serializers.CharField(read_only=True, source='new_overshoot.reason', default=None)
    
    def get_requested_by_label(self, obj):
        user = obj.requested_by
        if user is None:
            return None
        return f'{user.employee_id} - {user.name}'.strip(' -')

    class Meta:
        model = ChangeoverCorrectionRequest
        fields = [
            'id', 'changeover_id', 'requested_by_label', 'current_recipe',
            'old_category', 'old_reason', 
            'new_category', 'new_reason', 
            'status', 'created_at'
        ]



# ======================================================================
# Operator-wise setup capture
# ======================================================================

class DutySessionSerializer(serializers.ModelSerializer):
    """An operator's stint on the line."""

    employee_id = serializers.CharField(source='user.employee_id', read_only=True)
    operator_name = serializers.CharField(source='user.name', read_only=True)
    end_reason_display = serializers.CharField(
        source='get_end_reason_display', read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = DutySession
        fields = [
            'id', 'employee_id', 'operator_name',
            'shift', 'production_date',
            'started_at', 'ended_at', 'end_reason', 'end_reason_display',
            'is_open',
        ]
        read_only_fields = fields


class SetupAllocationSerializer(serializers.ModelSerializer):
    """One attributed piece of a setup window."""

    employee_id = serializers.SerializerMethodField()
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = SetupAllocation
        fields = [
            'id', 'changeover', 'employee_id', 'operator_name',
            'shift', 'production_date', 'segment_start', 'segment_end',
            'gross_minutes', 'interrupted_minutes', 'allocated_minutes',
            'is_manual',
        ]
        read_only_fields = fields

    # Nobody signed in is a real outcome, not missing data -- say so
    # rather than returning null and leaving the client to invent a label.
    def get_employee_id(self, obj):
        return obj.operator.employee_id if obj.operator_id else None

    def get_operator_name(self, obj):
        return obj.operator.name if obj.operator_id else 'Unattributed'


class LineStoppageSerializer(serializers.ModelSerializer):
    """A stretch when the line was stopped.

    Carries no changeover: a stoppage is recorded while it happens, and
    the changeover it affects may not have been detected yet. The link is
    made by overlap when the allocation is rebuilt.
    """

    category = serializers.CharField(source='reason.category', read_only=True)
    reason_label = serializers.CharField(source='reason.reason', read_only=True)
    recorded_by_label = serializers.SerializerMethodField()
    # The client knows the signed-in employee ID and nothing else about
    # them, so this is what it compares against to decide whether to
    # offer Remove. The API still enforces it.
    recorded_by_employee_id = serializers.CharField(
        source='recorded_by.employee_id', read_only=True, default=None)
    reviewed_by_label = serializers.SerializerMethodField()
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)
    duration_minutes = serializers.FloatField(read_only=True)
    is_running = serializers.BooleanField(read_only=True)
    counts = serializers.BooleanField(read_only=True)

    class Meta:
        model = LineStoppage
        fields = [
            'id', 'reason', 'category', 'reason_label', 'note',
            'started_at', 'ended_at', 'duration_minutes', 'is_running',
            'recorded_by_label', 'recorded_by_employee_id',
            'created_at', 'updated_at',
            'status', 'status_display', 'reviewed_by_label', 'reviewed_at',
            'review_note', 'counts', 'boundary_handover',
        ]
        read_only_fields = [
            'id', 'category', 'reason_label', 'duration_minutes', 'is_running',
            'recorded_by_label', 'recorded_by_employee_id',
            'created_at', 'updated_at',
            'status', 'status_display', 'reviewed_by_label', 'reviewed_at',
            'review_note', 'counts', 'boundary_handover',
        ]

    def get_recorded_by_label(self, obj):
        user = obj.recorded_by
        return f"{user.name} ({user.employee_id})" if user else None

    def get_reviewed_by_label(self, obj):
        user = obj.reviewed_by
        return f"{user.name} ({user.employee_id})" if user else None

    def validate(self, data):
        """Three rules. Containment is gone with the changeover.

        A stoppage no longer has to fall inside a setup window, because
        when it is recorded there may be no window yet -- that was the
        whole reason for decoupling it. The engine clips whatever it is
        given, so a stoppage spanning the start of a setup counts only
        its overlapping part.
        """
        from .setup_capture import as_naive_local

        instance = self.instance
        started = as_naive_local(
            data.get('started_at', getattr(instance, 'started_at', None)))
        ended = as_naive_local(
            data.get('ended_at', getattr(instance, 'ended_at', None)))

        # 1. It has to last. Identical times are a mis-click, not a stop.
        if started and ended:
            if ended == started:
                raise serializers.ValidationError(
                    {'ended_at': 'The line has to stop for some length of '
                                 'time -- these are the same moment.'})
            if ended < started:
                raise serializers.ValidationError(
                    {'ended_at': 'The stoppage cannot end before it '
                                 'started.'})

        # 2. The line cannot be stopped twice at once. Two overlapping
        #    stoppages are either a double entry or two people
        #    disagreeing; the engine caps the total so the arithmetic
        #    stays sane, which hides the problem rather than fixing it.
        if started:
            FAR_FUTURE = datetime.datetime.max
            stops = ended or FAR_FUTURE
            others = LineStoppage.objects.exclude(
                status=LineStoppage.Status.REJECTED)
            if instance is not None and instance.pk:
                others = others.exclude(pk=instance.pk)
            for other in others:
                other_start = as_naive_local(other.started_at)
                other_end = as_naive_local(other.ended_at) or FAR_FUTURE
                if other_start is None:
                    continue
                if started < other_end and other_start < stops:
                    shown = ('ongoing' if other.ended_at is None
                             else f'{other_end:%H:%M}')
                    raise serializers.ValidationError(
                        {'started_at':
                            f'The line is already recorded as stopped from '
                            f'{other_start:%d %b %H:%M} to {shown}.'})

        # 3. "Other" with no explanation is the same as no reason at all.
        reason = data.get('reason', getattr(instance, 'reason', None))
        note = data.get('note', getattr(instance, 'note', '') or '')
        if reason is not None and not note.strip():
            if 'other' in f"{reason.category} {reason.reason}".lower():
                raise serializers.ValidationError(
                    {'note': 'Describe the stoppage when the reason is '
                             '"Other".'})

        return data


class StoppageReviewSerializer(serializers.Serializer):
    """Approving or rejecting a recorded stoppage."""

    action = serializers.ChoiceField(choices=['approve', 'reject'])
    note = serializers.CharField(
        required=False, allow_blank=True, default='',
        help_text="Required when rejecting -- the person who recorded it "
                  "should know why.")

    def validate(self, data):
        if data['action'] == 'reject' and not data.get('note', '').strip():
            raise serializers.ValidationError(
                {'note': 'Say why you are rejecting it.'})
        return data


class SetupAttributionSerializer(serializers.ModelSerializer):
    """Assigning an unattributed stretch of setup to an operator."""

    employee_id = serializers.CharField(write_only=True)
    operator_label = serializers.SerializerMethodField()
    assigned_by_label = serializers.SerializerMethodField()

    class Meta:
        model = SetupAttribution
        fields = [
            'id', 'changeover', 'segment_start', 'employee_id',
            'operator_label', 'assigned_by_label', 'assigned_at', 'note',
        ]
        read_only_fields = [
            'id', 'operator_label', 'assigned_by_label', 'assigned_at',
        ]
        # The model's UniqueConstraint on (changeover, segment_start)
        # makes DRF attach a UniqueTogetherValidator, which refuses a
        # second assignment for the same segment. But reassigning is the
        # point -- the view upserts deliberately, so this validator
        # turns a correction into an error.
        validators = []

    def get_operator_label(self, obj):
        return f"{obj.operator.name} ({obj.operator.employee_id})"

    def get_assigned_by_label(self, obj):
        return f"{obj.assigned_by.name} ({obj.assigned_by.employee_id})"

    def validate_employee_id(self, value):
        from user_authentication.models import User, normalize_employee_id

        try:
            user = User.objects.get(
                employee_id=normalize_employee_id(value))
        except User.DoesNotExist:
            raise serializers.ValidationError('No such employee ID.')

        if user.status == User.Status.RETIRED:
            raise serializers.ValidationError(
                'That employee has been removed.')
        if user.role != User.Role.OPERATOR:
            # Setup time is operator-wise. Crediting a manager would put
            # a name in the report that the report is not about.
            raise serializers.ValidationError(
                'Setup time can only be assigned to an operator.')
        return user

    def validate(self, data):
        changeover = data.get(
            'changeover', getattr(self.instance, 'changeover', None))
        segment_start = data.get(
            'segment_start', getattr(self.instance, 'segment_start', None))

        if changeover is not None and segment_start is not None:
            from .setup_capture import as_naive_local
            moment = as_naive_local(segment_start)
            matching = changeover.allocations.filter(segment_start=moment)
            if not matching.exists():
                raise serializers.ValidationError(
                    {'segment_start': 'That is not one of this '
                                      "changeover's setup segments."})
            # Only the duty record is protected. A segment somebody
            # assigned by hand can be reassigned -- that is a correction,
            # and refusing it would mean the first guess were permanent.
            if matching.filter(operator__isnull=False,
                               is_manual=False).exists():
                raise serializers.ValidationError(
                    {'segment_start': 'That stretch is already credited to '
                                      'whoever was signed in for it.'})
        return data
