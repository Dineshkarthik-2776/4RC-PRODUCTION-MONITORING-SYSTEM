from rest_framework import serializers
from django.utils import timezone
from .models import ChangeoverSummary, RecipeMaster, ChangeoverCorrectionRequest, StandardTimeMaster, OvershootReasons, ChangeoverRule


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
    requested_by_email = serializers.EmailField(read_only=True, source='requested_by.email')
    current_recipe = serializers.CharField(read_only=True, source='changeover.current_recipe')
    
    old_category = serializers.CharField(read_only=True, source='old_overshoot.category', default=None)
    old_reason = serializers.CharField(read_only=True, source='old_overshoot.reason', default=None)
    new_category = serializers.CharField(read_only=True, source='new_overshoot.category', default=None)
    new_reason = serializers.CharField(read_only=True, source='new_overshoot.reason', default=None)
    
    class Meta:
        model = ChangeoverCorrectionRequest
        fields = [
            'id', 'changeover_id', 'requested_by_email', 'current_recipe',
            'old_category', 'old_reason', 
            'new_category', 'new_reason', 
            'status', 'created_at'
        ]

