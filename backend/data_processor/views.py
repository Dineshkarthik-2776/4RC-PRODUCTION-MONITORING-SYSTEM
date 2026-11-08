import datetime  # ✅ For datetime handling
import logging
import pandas as pd
import io
from django.db.models import Sum, Count, F, FloatField, Avg, Q, ExpressionWrapper
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.parsers import MultiPartParser, FormParser ,JSONParser
from rest_framework.permissions import IsAuthenticated
from user_authentication.models import User
from user_authentication.permissions import (
    CanApproveRequests,
    CanRecordOvershootReason,
    CanRecordSetupEvent,
    IsManager,
    IsManagerOrReadOnly,
)
from .models import (
    RecipeMaster, ChangeoverSummary, StandardTimeMaster, RawLineData,
    DutySession, LineStoppage, SetupAllocation, SetupAttribution,
)
from .setup_capture import (
    rebuild_allocations,
    rebuild_for_stoppage,
    stoppages_overlapping,
)
from .tasks import process_changeover_data
from .serializers import (
    RecipeMasterSerializer,
    RecipeMasterCreateSerializer,
    ChangeoverDetailSerializer,
    ChangeoverUpdateSerializer,
    StandardTimeSerializer,
    DutySessionSerializer,
    LineStoppageSerializer,
    SetupAllocationSerializer,
    SetupAttributionSerializer,
    StoppageReviewSerializer,
)

logger = logging.getLogger(__name__)


def _format_local_timestamp(value):
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    value = timezone.localtime(value)
    return value.strftime('%Y-%m-%d %H:%M:%S')


# ============================================================
# 🕒 Standard Time Master API
# ============================================================

class StandardTimeListCreateAPIView(generics.ListCreateAPIView):
    """
    Handles listing and saving (add/update) standard time records.
    """
    queryset = StandardTimeMaster.objects.all()
    serializer_class = StandardTimeSerializer
    permission_classes = [IsManager]

    def post(self, request, *args, **kwargs):
        data = request.data
        if isinstance(data, dict):
            data = [data]
            
        if isinstance(data, list):
            for item in data:
                changeover_key = item.get('changeover_key')
                standard_time = item.get('standard_time')
                if changeover_key and standard_time is not None:
                    obj, created = StandardTimeMaster.objects.update_or_create(
                        changeover_key=changeover_key,
                        defaults={'standard_time': standard_time}
                    )
                    if created:
                        obj.is_custom = True
                        obj.save()
            return Response({"message": "Standard times updated successfully."}, status=status.HTTP_200_OK)
        
        return Response({"error": "Invalid payload format."}, status=status.HTTP_400_BAD_REQUEST)

class StandardTimeDeleteAPIView(generics.DestroyAPIView):
    """
    Remove a mapping.
    """
    queryset = StandardTimeMaster.objects.all()
    serializer_class = StandardTimeSerializer
    permission_classes = [IsManager]
    
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not instance.is_custom:
            return Response(
                {"error": "Base changeover types cannot be deleted."}, 
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)

from .models import ChangeoverRule
from .serializers import ChangeoverRuleSerializer

class StandardTimeRuleAPIView(APIView):
    permission_classes = [IsManager]

    def get(self, request, pk, *args, **kwargs):
        try:
            master = StandardTimeMaster.objects.get(pk=pk)
        except StandardTimeMaster.DoesNotExist:
            return Response({"error": "Standard Time not found."}, status=404)
        
        rules = ChangeoverRule.objects.filter(standard_time_master=master)
        serializer = ChangeoverRuleSerializer(rules, many=True)
        return Response(serializer.data)

    def post(self, request, pk, *args, **kwargs):
        try:
            master = StandardTimeMaster.objects.get(pk=pk)
        except StandardTimeMaster.DoesNotExist:
            return Response({"error": "Standard Time not found."}, status=404)
            
        data = request.data
        serializer = ChangeoverRuleSerializer(data=data)
        if serializer.is_valid():
            serializer.save(standard_time_master=master)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)
        
class StandardTimeRuleDeleteAPIView(APIView):
    permission_classes = [IsManager]

    def delete(self, request, pk, rule_id, *args, **kwargs):
        try:
            rule = ChangeoverRule.objects.get(pk=rule_id, standard_time_master_id=pk)
            rule.delete()
            return Response(status=204)
        except ChangeoverRule.DoesNotExist:
            return Response({"error": "Rule not found."}, status=404)



# ============================================================
# 📁 Recipe Upload API
# ============================================================

# Shared with the pipeline -- see data_processor/recipe_codes.py.
from .recipe_codes import normalize_recipe_code as _normalize_recipe_code
from .recipe_codes import find_master_by_code, upsert_master_recipe


def _is_missing_recipe_type(recipe_type):
    val = str(recipe_type or '').strip().lower()
    return val in ['', 'unknown']


def _is_missing_target_speed(target_speed):
    return target_speed is None or float(target_speed) <= 0


def _requeue_skipped_records_for_recipe_codes(recipe_codes):
    """
    Requeue skipped rows only for recipe codes that are now valid in RecipeMaster.
    Matching is normalization-based to handle code variants like 'CAP 66' vs 'CAP66'.
    """
    normalized_candidates = {_normalize_recipe_code(code) for code in recipe_codes if _normalize_recipe_code(code)}
    if not normalized_candidates:
        return 0

    recipe_master_rows = RecipeMaster.objects.all().only('recipe_code', 'recipe_type', 'target_speed')
    valid_normalized = {
        _normalize_recipe_code(r.recipe_code)
        for r in recipe_master_rows
        if _normalize_recipe_code(r.recipe_code) in normalized_candidates
        and not _is_missing_recipe_type(r.recipe_type)
        and not _is_missing_target_speed(r.target_speed)
    }

    if not valid_normalized:
        return 0

    # Requeue by normalized recipe code for all rows that may need recalculation after data fix.
    candidate_rows = RawLineData.objects.filter(
        status__in=["SKIPPED_NO_TARGET_SPEED", "PENDING_RETRY", "SUCCESS", "FAILED_OPERATIONAL_SPEED"]
    ).only('id', 'recipe_code')
    ids_to_requeue = [
        row.id
        for row in candidate_rows
        if _normalize_recipe_code(row.recipe_code) in valid_normalized
    ]

    if not ids_to_requeue:
        return 0

    return RawLineData.objects.filter(id__in=ids_to_requeue).update(
        processed_flag=False,
        status="PENDING_RETRY",
    )

class RecipeMasterAPIView(generics.ListCreateAPIView):
    """
    API endpoint to list all recipes and create a single recipe.
    """
    queryset = RecipeMaster.objects.all()
    serializer_class = RecipeMasterSerializer
    permission_classes = [IsManagerOrReadOnly]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return RecipeMasterCreateSerializer
        return RecipeMasterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        recipe_code = serializer.validated_data.get('recipe_code')
        if RecipeMaster.objects.filter(recipe_code=recipe_code).exists():
            return Response(
                {"error": f"Recipe '{recipe_code}' already exists."},
                status=status.HTTP_400_BAD_REQUEST
            )

        self.perform_create(serializer)
        recipe_code = serializer.validated_data.get('recipe_code')
        requeued_count = _requeue_skipped_records_for_recipe_codes([recipe_code])
        task_id = None
        if requeued_count > 0:
            task_id = process_changeover_data.delay().id
        return Response(
            {
                "message": "Recipe created successfully.",
                "data": serializer.data,
                "requeued_count": requeued_count,
                "processing_task_id": task_id,
            },
            status=status.HTTP_201_CREATED
        )


class RecipeMasterUpdateAPIView(generics.UpdateAPIView):
    """
    API endpoint to update recipe_type and target_speed.
    recipe_code and sap_code are read-only.
    """
    queryset = RecipeMaster.objects.all()
    serializer_class = RecipeMasterSerializer
    permission_classes = [IsManager]

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        old_missing_type = _is_missing_recipe_type(instance.recipe_type)
        old_missing_speed = _is_missing_target_speed(instance.target_speed)

        response = super().update(request, *args, **kwargs)

        instance.refresh_from_db()
        type_became_available = old_missing_type and not _is_missing_recipe_type(instance.recipe_type)
        speed_became_available = old_missing_speed and not _is_missing_target_speed(instance.target_speed)

        if type_became_available or speed_became_available:
            requeued_count = _requeue_skipped_records_for_recipe_codes([instance.recipe_code])
            if requeued_count > 0:
                process_changeover_data.delay()

        return response


class RecipeUploadAPIView(APIView):
    """
    Upload recipe data using:
    1) multipart file field `excel_file` (csv/xls/xlsx)
    2) JSON body with `data: [{recipe_code, recipe_type, target_speed, sap_code?}, ...]`
    """
    permission_classes = [IsManager]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        try:
            records = []

            if 'excel_file' in request.FILES:
                uploaded_file = request.FILES['excel_file']
                file_name = uploaded_file.name.lower()

                if file_name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                elif file_name.endswith('.xls') or file_name.endswith('.xlsx'):
                    df = pd.read_excel(uploaded_file)
                else:
                    return Response(
                        {"error": "Unsupported file format. Upload CSV, XLS, or XLSX."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                required_cols = {'recipe_code', 'recipe_type', 'target_speed'}
                missing_cols = required_cols.difference(set(df.columns))
                if missing_cols:
                    return Response(
                        {"error": f"Missing required columns: {', '.join(sorted(missing_cols))}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                records = df.to_dict(orient='records')
            else:
                payload = request.data.get('data', request.data)
                if isinstance(payload, dict):
                    payload = [payload]

                if not isinstance(payload, list):
                    return Response(
                        {"error": "Invalid payload. Send a list or `data` list."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                records = payload

            created_count = 0
            updated_count = 0
            failed_rows = []

            for idx, row in enumerate(records, start=1):
                recipe_code = str((row.get('recipe_code') or '')).strip()
                recipe_type = str((row.get('recipe_type') or '')).strip()
                target_speed_raw = row.get('target_speed')
                sap_code_raw = row.get('sap_code')

                if not recipe_code or not recipe_type or target_speed_raw in [None, '']:
                    failed_rows.append({"row": idx, "error": "recipe_code, recipe_type, and target_speed are required."})
                    continue

                try:
                    target_speed = float(target_speed_raw)
                except (TypeError, ValueError):
                    failed_rows.append({"row": idx, "error": "target_speed must be numeric."})
                    continue

                if target_speed < 1:
                    failed_rows.append({"row": idx, "error": "target_speed must be 1 or greater."})
                    continue

                defaults = {
                    'recipe_type': recipe_type,
                    'target_speed': target_speed,
                }

                sap_code = str(sap_code_raw).strip() if sap_code_raw is not None else ''
                if sap_code:
                    defaults['sap_code'] = sap_code

                # Match on the normalised code so re-uploading a sheet that
                # spells a recipe differently updates the existing master row
                # rather than creating a duplicate.
                obj, created = upsert_master_recipe(recipe_code, defaults=defaults)
                if obj is None:
                    failed_rows.append({"row": idx, "error": "recipe_code is empty after normalisation."})
                    continue

                if created:
                    created_count += 1
                else:
                    updated_count += 1

            affected_recipe_codes = [
                str((row.get('recipe_code') or '')).strip()
                for row in records
                if str((row.get('recipe_code') or '')).strip()
            ]
            requeued_count = _requeue_skipped_records_for_recipe_codes(affected_recipe_codes)
            task_id = None
            if requeued_count > 0:
                task_id = process_changeover_data.delay().id

            processed_count = created_count + updated_count
            if processed_count == 0:
                return Response(
                    {
                        "error": "No valid rows to process.",
                        "failed_rows": failed_rows,
                        "total_rows": len(records),
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            return Response(
                {
                    "message": "Recipe upload processed successfully.",
                    "count": processed_count,
                    "created": created_count,
                    "updated": updated_count,
                    "requeued_count": requeued_count,
                    "processing_task_id": task_id,
                    "total_rows": len(records),
                    "failed_rows": len(failed_rows),
                    "errors": failed_rows,
                },
                status=status.HTTP_200_OK
            )
        except Exception as exc:
            return Response(
                {
                    "error": "Failed to process recipe upload.",
                    "details": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class RecipeExportAPIView(APIView):
    """
    API endpoint returning the full RecipeMaster as JSON.

    The CSV and Excel branches that used to live here were unreachable:
    `format` is a reserved DRF content-negotiation parameter, so
    ?format=csv and ?format=excel returned HTTP 404 ("Not found.") before
    this view ever ran. Only JSON was ever served. The spreadsheet exports
    the UI actually offers are /api/changeover-export/ and
    /api/kepware-logs-export/, which take no `format` parameter.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            recipes = RecipeMaster.objects.all()

            if not recipes.exists():
                return Response(
                    {"message": "No recipes found.", "data": []},
                    status=status.HTTP_200_OK
                )

            data = {
                r.recipe_code: {
                    "sap_code": r.sap_code,
                    "type": r.recipe_type,
                    "target_speed": r.target_speed
                }
                for r in recipes
            }
            return Response(data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {
                    "success": False,
                    "error": "Failed to generate export.",
                    "details": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# ============================================================
# ⚠️ Missing Recipe Warning API
# ============================================================

class MissingRecipeWarningAPIView(APIView):
    """
    API endpoint to fetch distinct recipe codes that caused data to be skipped
    due to missing target speeds in the last 3 days.
    """
    permission_classes = [IsManager]

    def get(self, request, *args, **kwargs):
        from .models import RawLineData
        from django.utils import timezone
        
        three_days_ago = timezone.now() - datetime.timedelta(days=3)
        
        warning_statuses = ["SKIPPED_NO_TARGET_SPEED", "PENDING_RETRY"]
        missing_recipes_qs = RawLineData.objects.filter(
            status__in=warning_statuses,
            timestamp__gte=three_days_ago
        )

        missing_recipes = list(missing_recipes_qs.values_list('recipe_code', flat=True).distinct())

        status_summary = {
            key: missing_recipes_qs.filter(status=key).count()
            for key in warning_statuses
        }
        
        clean_unique_recipes = sorted({
            str(r).strip() for r in missing_recipes if str(r).strip()
        })
        
        return Response({
            "warning": bool(clean_unique_recipes),
            "message": "These recipes have run on the machine but are missing target speeds in RecipeMaster. Please upload them to prevent data loss." if clean_unique_recipes else "All running recipes have target speeds.",
            "missing_recipe_codes": clean_unique_recipes,
            "statuses": status_summary,
        }, status=status.HTTP_200_OK)


class ChangeoverStatsAPIView(APIView):
    """
    API endpoint to retrieve all stats for the frontend dashboard:
    1. Grouped table data (for sampleData)
    2. Grouped bar chart data (for Overshoot Categories)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # 1️⃣ Get date parameters
        from_date_str = request.query_params.get('from_date', None)
        to_date_str = request.query_params.get('to_date', None)
        logger.info(
            "ChangeoverStats request received with from_date=%s to_date=%s",
            from_date_str,
            to_date_str,
        )

        # 2️⃣ Default: show all available shifts for calendar yesterday and today
        if not from_date_str and not to_date_str:
            # USE_TZ is False, so timezone.now() returns a naive datetime and
            # timezone.localtime() rejects it. datetime.now() is the correct
            # call for this project -- see the note in reports/views.py.
            now_local = datetime.datetime.now()
            from_date_str = (now_local.date() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            to_date_str = now_local.date().strftime('%Y-%m-%d')
            logger.info("Using default date window: %s to %s", from_date_str, to_date_str)

        # 3️⃣ Base queryset
        all_summaries = ChangeoverSummary.objects.all()
        logger.info("Changeover summaries before filter: %s", all_summaries.count())

        # 4️⃣ Apply date filtering
        if from_date_str and to_date_str:
            try:
                from_date_obj = datetime.datetime.strptime(from_date_str, '%Y-%m-%d').date()
                to_date_obj = datetime.datetime.strptime(to_date_str, '%Y-%m-%d').date()
                logger.info(
                    "Applying 7am-to-7am recipe_change_time filter from %s to %s",
                    from_date_obj,
                    to_date_obj,
                )

                # Production day window: 07:00 on from_date to 07:00 on (to_date + 1 day)
                from_dt_fallback = timezone.make_aware(datetime.datetime.combine(from_date_obj, datetime.time(7, 0)))
                to_dt_fallback = timezone.make_aware(datetime.datetime.combine(to_date_obj + datetime.timedelta(days=1), datetime.time(7, 0)))

                all_summaries = all_summaries.filter(
                    recipe_change_time__gte=from_dt_fallback,
                    recipe_change_time__lt=to_dt_fallback,
                )
                logger.info("Summaries after date filter: %s", all_summaries.count())

            except ValueError:
                return Response(
                    {"error": "Invalid date format. Please use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # 5️⃣ Apply Shift Filtering
        shift = request.query_params.get('shift', None)
        if shift:
            shift = shift.upper()
            logger.info("Applying shift filter: %s", shift)
            if shift in ['A', 'B', 'C']:
                if shift == 'A':
                    fallback_filter = Q(recipe_change_time__time__range=('07:00', '15:00'))
                elif shift == 'B':
                    fallback_filter = Q(recipe_change_time__time__range=('15:00', '23:00'))
                else: # C
                    fallback_filter = Q(recipe_change_time__time__gte='23:00') | Q(recipe_change_time__time__lt='07:00')
                
                all_summaries = all_summaries.filter(
                    Q(shift=shift) | (Q(shift__isnull=True) & fallback_filter)
                )
            
            logger.info("Summaries after shift filter: %s", all_summaries.count())

        # --- 1️⃣ Build grouped summary for table ---

        grouped_summary = (
            all_summaries
            .values('change_over_type')
            .annotate(
                Std=Avg('standard_time'),
                act=Avg('setup_time_actual'),
                static=Avg('static_setup_time'),
                ramp=Avg('ramp_up_time'),
                shoot=Avg(
                    ExpressionWrapper(
                        F('setup_time_actual') - F('standard_time'),
                        output_field=FloatField(),
                    )
                ),
                count=Count('id')
            )
            .order_by('change_over_type')
        )

        grouped_summary_list = list(grouped_summary)
        logger.info("Grouped table categories generated: %s", len(grouped_summary_list))

        # --- 2️⃣ Build structured table data ---
        all_individual_summaries = list(all_summaries)
        logger.info("Individual summary rows prepared: %s", len(all_individual_summaries))

        table_data_list = []
        for idx, group in enumerate(grouped_summary_list, start=1):
            raw_change_type = group['change_over_type']
            is_unknown_type = not raw_change_type
            change_type = raw_change_type or 'Unknown'

            # Python-side filtering (FAST)
            # Find all objects that match this group
            if is_unknown_type:
                summaries_for_type = [
                    summary for summary in all_individual_summaries
                    if not summary.change_over_type
                ]
            else:
                summaries_for_type = [
                    summary for summary in all_individual_summaries
                    if summary.change_over_type == change_type
                ]

            detail_serializer = ChangeoverDetailSerializer(summaries_for_type, many=True)
            details_data = detail_serializer.data

            table_data_list.append({
                "id": idx,
                "type": change_type,
                "Std": round(group['Std'] or 0, 2),
                "act": round(group['act'] or 0, 2),
                "static": round(group['static'] or 0, 2),
                "ramp": round(group['ramp'] or 0, 2),
                "shoot": round(group['shoot'] or 0, 2), # This line now uses the corrected 'shoot' value
                "count": group['count'],
                "details": details_data
            })
        logger.info("Final table_data groups count: %s", len(table_data_list))

        # --- 3️⃣ Build bar chart data ---

        bar_chart_data = list(
            all_summaries
            .filter(overshoot__isnull=False)
            .exclude(overshoot__category='None')
            .exclude(overshoot__category='Other')
            .values(category=F('overshoot__category'))
            .annotate(value=Count('id'))
            .order_by('-value')
        )
        logger.info("Final bar_chart categories count: %s", len(bar_chart_data))

        # --- 4️⃣ Return final response ---
        final_response = {
            "table_data": table_data_list,
            "bar_chart_data": bar_chart_data
        }
        return Response(final_response, status=status.HTTP_200_OK)

class ChangeoverExportAPIView(APIView):
    """
    API endpoint to export individual changeover records as Excel or CSV.
    Supports from_date, to_date, shift, and format filters.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            # 1. Get filters
            from_date_str = request.query_params.get('from_date')
            to_date_str = request.query_params.get('to_date')
            shift = request.query_params.get('shift')
            export_format = request.query_params.get('format', 'excel').lower()

            # 2. Base Queryset with join
            queryset = ChangeoverSummary.objects.select_related('overshoot').all()

            # 3. Apply Date Filtering (same 7am-to-7am logic as Stats view)
            if from_date_str and to_date_str:
                from_date_obj = datetime.datetime.strptime(from_date_str, '%Y-%m-%d').date()
                to_date_obj = datetime.datetime.strptime(to_date_str, '%Y-%m-%d').date()

                from_dt_fallback = timezone.make_aware(datetime.datetime.combine(from_date_obj, datetime.time(7, 0)))
                to_dt_fallback = timezone.make_aware(datetime.datetime.combine(to_date_obj + datetime.timedelta(days=1), datetime.time(7, 0)))

                queryset = queryset.filter(
                    recipe_change_time__gte=from_dt_fallback,
                    recipe_change_time__lt=to_dt_fallback,
                )

            logger.info(
                "ChangeoverExport filtered count (from_date=%s to_date=%s shift=%s format=%s): %s",
                from_date_str,
                to_date_str,
                shift,
                export_format,
                queryset.count(),
            )

            # 4. Apply Shift Filtering
            if shift:
                shift = shift.upper()
                if shift in ['A', 'B', 'C']:
                    if shift == 'A':
                        fallback_filter = Q(recipe_change_time__time__range=('07:00', '15:00'))
                    elif shift == 'B':
                        fallback_filter = Q(recipe_change_time__time__range=('15:00', '23:00'))
                    else: # C
                        fallback_filter = Q(recipe_change_time__time__gte='23:00') | Q(recipe_change_time__time__lt='07:00')
                    
                    queryset = queryset.filter(Q(shift=shift) | (Q(shift__isnull=True) & fallback_filter))

            # 5. Prepare Data for Excel
            data = []
            for item in queryset:
                # Calculate overshoot (Shoot = Act - Std)
                shoot = None
                if item.setup_time_actual is not None and item.standard_time is not None:
                    shoot = item.setup_time_actual - item.standard_time

                # Helper to format values as N/A if None
                def val(x):
                    return x if x is not None else "N/A"

                data.append({
                    'Previous Recipe': val(item.previous_recipe),
                    'Current Recipe': val(item.current_recipe),
                    'Type of Style': val(item.change_over_type),
                    'Production Date': val(item.production_date.strftime('%Y-%m-%d') if item.production_date else None),
                    'Shift': val(item.shift),
                    'Std Time (min)': val(item.standard_time),
                    'Act Time (min)': val(item.setup_time_actual),
                    'Static S/U (min)': val(item.static_setup_time),
                    'Ramp Up (min)': val(item.ramp_up_time),
                    'Over Shoot (min)': val(shoot),
                    'Start Time': val(_format_local_timestamp(item.recipe_change_time)),
                    'Category': val(item.overshoot.category if item.overshoot else None),
                    'Reason': val(item.overshoot.reason if item.overshoot else None),
                    'Remarks': val(item.remarks),
                })

            if not data:
                return Response({"error": "No data found for the selected filters."}, status=status.HTTP_404_NOT_FOUND)

            # 6. Create CSV (if requested) or Excel
            df = pd.DataFrame(data)

            if export_format == 'csv':
                response = HttpResponse(content_type='text/csv')
                filename = f"Changeover_Report_{from_date_str}_to_{to_date_str}.csv" if from_date_str else "Changeover_Report.csv"
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                df.to_csv(path_or_buf=response, index=False)
                return response
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Changeovers')
                
                workbook = writer.book
                worksheet = writer.sheets['Changeovers']

                # Define formats
                center_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
                header_fmt = workbook.add_format({
                    'bg_color': '#1F4E78',
                    'font_color': 'white',
                    'bold': True,
                    'align': 'center',
                    'valign': 'vcenter',
                    'border': 1
                })
                na_fmt = workbook.add_format({
                    'align': 'center',
                    'valign': 'vcenter',
                    'font_color': '#F97316', # Orange color like UI
                    'bold': True,
                    'border': 1
                })

                # Apply header format
                for col_num, value in enumerate(df.columns.values):
                    worksheet.write(0, col_num, value, header_fmt)

                # Auto-adjust columns width and apply center format to data
                for i, col in enumerate(df.columns):
                    column_len = max(df[col].astype(str).str.len().max(), len(col)) + 4
                    worksheet.set_column(i, i, column_len, center_fmt)
                    
                    # Apply conditional formatting for N/A values
                    worksheet.conditional_format(1, i, len(df), i, {
                        'type':     'cell',
                        'criteria': '==',
                        'value':    '"N/A"',
                        'format':   na_fmt
                    })

            buffer.seek(0)
            response = HttpResponse(
                buffer,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            filename = f"Changeover_Report_{from_date_str}_to_{to_date_str}.xlsx" if from_date_str else "Changeover_Report.xlsx"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        except Exception as e:
            return Response({"error": f"Failed to generate Excel: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class KepwareLogsExportAPIView(APIView):
    """
    Export Kepware (FRC) raw logs for a single production date.
    Uses a 07:00 to 07:00 window to cover all three shifts.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        date_str = request.query_params.get('date')
        if not date_str:
            return Response({"error": "Missing required parameter: date (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        from_dt = timezone.make_aware(datetime.datetime.combine(date_obj, datetime.time(7, 0)))
        to_dt = timezone.make_aware(datetime.datetime.combine(date_obj + datetime.timedelta(days=1), datetime.time(7, 0)))

        logs_qs = RawLineData.objects.filter(
            timestamp__gte=from_dt,
            timestamp__lt=to_dt,
        ).order_by('timestamp')

        if not logs_qs.exists():
            return Response({"error": "No logs found for the selected date."}, status=status.HTTP_404_NOT_FOUND)

        data = []
        for row in logs_qs:
            data.append({
                'MachineID': row.machine_id,
                'RecipeCode': row.recipe_code,
                'LineSpeedActual': row.line_speed_actual,
                'LineSpeedSet': row.line_speed_set,
                'TimeStamp': _format_local_timestamp(row.timestamp),
                'DateCreated': _format_local_timestamp(row.date_created),
                'Status': row.status,
                'ProcessedFlag': row.processed_flag,
            })

        df = pd.DataFrame(data)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Kepware Logs')

            workbook = writer.book
            worksheet = writer.sheets['Kepware Logs']
            header_fmt = workbook.add_format({
                'bg_color': '#1F4E78',
                'font_color': 'white',
                'bold': True,
                'align': 'center',
                'valign': 'vcenter',
                'border': 1
            })
            center_fmt = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})

            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_fmt)
                column_len = max(df.iloc[:, col_num].astype(str).str.len().max(), len(value)) + 4
                worksheet.set_column(col_num, col_num, column_len, center_fmt)

        buffer.seek(0)
        response = HttpResponse(
            buffer,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = (
            f'attachment; filename="Kepware_Logs_{date_str}.xlsx"'
        )
        return response


# ============================================================
# ✏️ Changeover Update API
# ============================================================

class ChangeoverUpdateAPIView(generics.UpdateAPIView):
    """
    API endpoint for updates.
    - Managers: Can overwrite anytime.
    - Workers:
        - First time entry: Allowed.
        - < 5 hours since setup_complete: Allowed.
        - > 5 hours: PROCESSED AS REQUEST (creates ChangeoverCorrectionRequest).
    """
    queryset = ChangeoverSummary.objects.all()
    serializer_class = ChangeoverUpdateSerializer
    permission_classes = [CanRecordOvershootReason]

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()

        # 1. Validation (Same as before)
        if instance.setup_time_actual is None or instance.standard_time is None:
            return Response(
                {"error": "Cannot add reason. Time data is not calculated."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if instance.setup_time_actual <= instance.standard_time:
            return Response(
                {"error": "No overshoot occurred. A reason and category cannot be added."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Check Role
        # Managers write directly whatever the elapsed time. A shift incharge
        # is bound by the 5-hour window below. Operators never reach here --
        # CanRecordOvershootReason refuses them.
        user = request.user
        is_manager = user.is_manager

        # 3. Manager Logic
        if is_manager:
            return super().partial_update(request, *args, **kwargs)

        # 4. Worker Logic
        # ⚠️ TESTING OVERRIDE: Allow creating a request even if valid, for validation
        force_test = request.query_params.get('test', 'false').lower() == 'true'

        if not force_test:
            # Case A: First time entry? (overshoot is null)
            if not instance.overshoot_id:
                return super().partial_update(request, *args, **kwargs)

            # Case B: Within 5 hours window?
            # Use setup_complete_timestamp or fallback to recipe_change_time
            base_time = instance.setup_complete_timestamp or instance.recipe_change_time
            
            if base_time:
                 # Ensure base_time is offset-aware/naive matching timezone.now()
                 # base_time is expected to be timezone-aware in local timezone.
                 now = timezone.now()
                 diff = now - base_time
                 hours_elapsed = diff.total_seconds() / 3600.0

                 if hours_elapsed <= 5.0:
                     return super().partial_update(request, *args, **kwargs)

        # Case C: Window expired -> Create CORRECTION REQUEST
        new_overshoot_id = request.data.get('overshoot')

        if not new_overshoot_id:
            return Response(
                {"error": "Overshoot reason is required for a request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create the request
        from .models import ChangeoverCorrectionRequest
        
        correction_req = ChangeoverCorrectionRequest.objects.create(
            changeover=instance,
            requested_by=user,
            old_overshoot=instance.overshoot,
            new_overshoot_id=new_overshoot_id,
            status=ChangeoverCorrectionRequest.Status.PENDING
        )

        return Response(
            {
                "message": "Time window expired. Correction request submitted for Manager approval.",
                "request_id": correction_req.id
            },
            status=status.HTTP_202_ACCEPTED
        )


# ============================================================
# 🛡️ Manager Approval Views
# ============================================================
from .serializers import ChangeoverCorrectionRequestSerializer
from .models import ChangeoverCorrectionRequest

class CorrectionRequestListAPIView(generics.ListAPIView):
    """
    List all PENDING correction requests.
    Shift incharges and managers.
    """
    permission_classes = [CanApproveRequests]
    serializer_class = ChangeoverCorrectionRequestSerializer

    def get_queryset(self):
        return ChangeoverCorrectionRequest.objects.filter(status=ChangeoverCorrectionRequest.Status.PENDING)


class CorrectionRequestActionAPIView(APIView):
    """
    Approve or Reject a correction request.
    Shift incharges and managers -- the people who run the shift are the
    ones who know whether the claim is true.
    """
    permission_classes = [CanApproveRequests]

    def post(self, request, pk, *args, **kwargs):
        try:
            correction_req = ChangeoverCorrectionRequest.objects.get(pk=pk)
        except ChangeoverCorrectionRequest.DoesNotExist:
            return Response({"error": "Request not found."}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action') # 'approve' or 'reject'

        if correction_req.status != ChangeoverCorrectionRequest.Status.PENDING:
             return Response({"error": "Request is already processed."}, status=status.HTTP_400_BAD_REQUEST)

        if action == 'approve':
            # 1. Update the actual ChangeoverSummary
            summary = correction_req.changeover
            summary.overshoot = correction_req.new_overshoot
            summary.save()

            # 2. Mark request as APPROVED
            correction_req.status = ChangeoverCorrectionRequest.Status.APPROVED
            correction_req.save()
            
            return Response({"message": "Request Approved and Updated."}, status=status.HTTP_200_OK)

        elif action == 'reject':
            correction_req.status = ChangeoverCorrectionRequest.Status.REJECTED
            correction_req.save()
            return Response({"message": "Request Rejected."}, status=status.HTTP_200_OK)

        else:
            return Response({"error": "Invalid action. Use 'approve' or 'reject'."}, status=status.HTTP_400_BAD_REQUEST)

# ============================================================
# ⚙️ Overshoot Options API
# ============================================================
from .models import OvershootReasons
from .serializers import OvershootReasonsSerializer

class OvershootOptionsAPIView(generics.ListAPIView):
    """
    API endpoint to retrieve all overshoot reasons and categories.
    Used by the frontend to populate dropdowns.
    """
    permission_classes = [IsAuthenticated]
    queryset = OvershootReasons.objects.all()
    serializer_class = OvershootReasonsSerializer



# ======================================================================
# Operator-wise setup capture
# ======================================================================

def _parse_date(value, fallback=None):
    """A YYYY-MM-DD query parameter, or the fallback if absent or junk."""
    if not value:
        return fallback
    try:
        return datetime.datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return fallback


class LineStoppageListCreateAPIView(generics.ListCreateAPIView):
    """Record that the line has stopped -- while it is stopped.

    This is the live entry point. An operator presses it the moment the
    line goes down, picks a reason, and leaves `ended_at` empty; they
    close it when the line runs again. No changeover is named, because
    at that moment the changeover does not exist yet -- the pipeline
    only writes one once a setup window has completed.

    Filters: ?status=pending, ?active=true for the open one,
    ?from= / ?to= for a window.

    Operators may post here even though they are read-only elsewhere.
    They are the people standing at the machine when it stops, and a
    stoppage nobody can record silently inflates their own setup figure.
    """

    serializer_class = LineStoppageSerializer
    permission_classes = [CanRecordSetupEvent]

    def get_queryset(self):
        params = self.request.query_params
        queryset = LineStoppage.objects.select_related(
            'reason', 'recorded_by', 'reviewed_by')

        state = params.get('status')
        if state:
            queryset = queryset.filter(status=state)
        if params.get('active') in ('1', 'true', 'True'):
            queryset = queryset.filter(ended_at__isnull=True)

        start = _parse_date(params.get('from'))
        if start:
            queryset = queryset.filter(started_at__gte=
                                       datetime.datetime.combine(
                                           start, datetime.time.min))
        finish = _parse_date(params.get('to'))
        if finish:
            queryset = queryset.filter(started_at__lt=
                                       datetime.datetime.combine(
                                           finish + datetime.timedelta(days=1),
                                           datetime.time.min))
        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        # Somebody who could approve it is the person who would sign it
        # off anyway, so asking them to approve their own entry is
        # theatre. Operators wait for review.
        auto = user.role in (User.Role.MANAGER, User.Role.SHIFT_INCHARGE)
        stoppage = serializer.save(
            recorded_by=user,
            status=(LineStoppage.Status.APPROVED if auto
                    else LineStoppage.Status.PENDING),
            reviewed_by=user if auto else None,
            reviewed_at=datetime.datetime.now() if auto else None,
        )
        # There is no foreign key to follow, so every changeover whose
        # setup window this touches has to be found by overlap.
        rebuild_for_stoppage(stoppage)


class LineStoppageDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Close, correct or withdraw a stoppage.

    Closing one is a PATCH with ended_at -- what the "line running
    again" button sends.
    """

    queryset = LineStoppage.objects.select_related(
        'reason', 'recorded_by', 'reviewed_by')
    serializer_class = LineStoppageSerializer
    permission_classes = [CanRecordSetupEvent]

    def perform_update(self, serializer):
        before = self.get_object()
        previous_end = before.ended_at
        stoppage = serializer.save()
        rebuild_for_stoppage(stoppage)
        # Shortening a stoppage releases minutes in changeovers it no
        # longer touches, so those have to be recomputed as well.
        if previous_end and stoppage.ended_at != previous_end:
            rebuild_for_stoppage(
                LineStoppage(started_at=before.started_at,
                             ended_at=previous_end))

    def perform_destroy(self, instance):
        snapshot = LineStoppage(started_at=instance.started_at,
                                ended_at=instance.ended_at)
        instance.delete()
        rebuild_for_stoppage(snapshot)


class CurrentStoppageAPIView(APIView):
    """Is the line stopped right now, and since when?

    What the header button reads to decide whether it says "Line
    stopped" or "Line running again".
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        open_stoppage = LineStoppage.objects.filter(
            ended_at__isnull=True
        ).exclude(
            status=LineStoppage.Status.REJECTED
        ).select_related('reason', 'recorded_by').order_by('-started_at').first()

        # A stoppage the shift-end task closed, which nobody has yet
        # said was resolved. The incoming shift is asked about it.
        handover = LineStoppage.objects.filter(
            boundary_handover='pending'
        ).select_related('reason').order_by('-ended_at').first()

        return Response({
            'now': datetime.datetime.now().isoformat(),
            'stopped': open_stoppage is not None,
            'stoppage': (LineStoppageSerializer(open_stoppage).data
                         if open_stoppage else None),
            'handover': (LineStoppageSerializer(handover).data
                         if handover and open_stoppage is None else None),
        })


class StoppageHandoverAPIView(APIView):
    """The incoming shift answering "was the line still down?".

    Answering CONTINUED opens a fresh stoppage starting at the boundary
    the previous one was closed at, so the minutes between are not lost
    and each shift still owns its own record.
    """

    permission_classes = [CanRecordSetupEvent]

    def post(self, request, pk):
        try:
            previous = LineStoppage.objects.select_related('reason').get(pk=pk)
        except LineStoppage.DoesNotExist:
            return Response({'error': 'No such stoppage.'},
                            status=status.HTTP_404_NOT_FOUND)

        if previous.boundary_handover != 'pending':
            return Response(
                {'error': 'That stoppage has already been handed over.'},
                status=status.HTTP_400_BAD_REQUEST)

        answer = request.data.get('answer')
        if answer not in ('continued', 'ended'):
            return Response(
                {'error': "answer must be 'continued' or 'ended'."},
                status=status.HTTP_400_BAD_REQUEST)

        previous.boundary_handover = answer
        previous.save(update_fields=['boundary_handover'])

        if answer == 'ended':
            return Response({'message': 'Noted -- the line had resumed.',
                             'stoppage': None}, status=status.HTTP_200_OK)

        if LineStoppage.objects.filter(ended_at__isnull=True).exists():
            return Response(
                {'error': 'The line is already recorded as stopped.'},
                status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        auto = user.role in (User.Role.MANAGER, User.Role.SHIFT_INCHARGE)
        carried = LineStoppage.objects.create(
            reason=previous.reason,
            note=previous.note,
            started_at=previous.ended_at,
            recorded_by=user,
            status=(LineStoppage.Status.APPROVED if auto
                    else LineStoppage.Status.PENDING),
            reviewed_by=user if auto else None,
            reviewed_at=datetime.datetime.now() if auto else None,
        )
        rebuild_for_stoppage(carried)

        return Response({'message': 'Carried over from the shift change.',
                         'stoppage': LineStoppageSerializer(carried).data},
                        status=status.HTTP_201_CREATED)


class StoppageReviewAPIView(APIView):
    """Approve or reject a recorded stoppage.

    A stoppage subtracts minutes from an operator's setup figure, so it
    is a claim about that operator's performance -- the same kind of
    claim as an overshoot reason, and it goes to the same people. Until
    it is approved it changes no number.
    """

    permission_classes = [CanApproveRequests]

    def post(self, request, pk):
        try:
            stoppage = LineStoppage.objects.get(pk=pk)
        except LineStoppage.DoesNotExist:
            return Response({'error': 'No such stoppage.'},
                            status=status.HTTP_404_NOT_FOUND)

        serializer = StoppageReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']

        stoppage.status = (
            LineStoppage.Status.APPROVED if action == 'approve'
            else LineStoppage.Status.REJECTED)
        stoppage.reviewed_by = request.user
        stoppage.reviewed_at = datetime.datetime.now()
        stoppage.review_note = serializer.validated_data.get('note', '')
        stoppage.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_note'])

        # Approving makes the minutes stop counting; rejecting gives them
        # back. Either way the split is recomputed now rather than at the
        # next pipeline pass.
        rebuild_for_stoppage(stoppage)

        return Response(LineStoppageSerializer(stoppage).data,
                        status=status.HTTP_200_OK)


class DutyStatusAPIView(APIView):
    """Who is on the line right now, and when their shift ends.

    Readable by anyone signed in: a shift incharge taking over wants to
    know who is still showing as on duty.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        open_sessions = DutySession.objects.filter(
            ended_at__isnull=True).select_related('user').order_by('started_at')
        return Response({
            'now': datetime.datetime.now().isoformat(),
            'on_duty': DutySessionSerializer(open_sessions, many=True).data,
        })


class DutySessionListAPIView(generics.ListAPIView):
    """Duty history, for checking an operator's attributed time.

    ?start_date / ?end_date filter on production date, ?employee_id on the
    person.
    """

    serializer_class = DutySessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        params = self.request.query_params
        today = datetime.datetime.now().date()
        start = _parse_date(params.get('start_date'),
                            today - datetime.timedelta(days=7))
        end = _parse_date(params.get('end_date'), today)

        queryset = DutySession.objects.select_related('user').filter(
            production_date__gte=start, production_date__lte=end)

        employee_id = params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(user__employee_id=employee_id.upper())
        return queryset


class SetupTimeSummaryAPIView(APIView):
    """Setup time grouped the way the dashboard asks for it.

    ?group_by=operator (default), shift, date or machine
    ?start_date / ?end_date  production dates, inclusive
    ?shift=A|B|C
    ?recipe=  the changeover's current recipe, matched loosely

    The same three filters the dashboard toolbar carries, so switching to
    this tab keeps whatever the person was already looking at. Recipe has
    to be filtered here rather than in the browser as the other tabs do:
    this is a server-side aggregate, and filtering totals after the fact
    is not possible.

    Reports allocated minutes -- the setup window less any recorded
    interruption -- alongside the gross and interrupted figures, because
    "two hours of setup, forty minutes of it a breakdown" is a different
    story from two hours of setup, and an operator judged on the first
    number deserves to see the second.

    Setup efficiency compares the allocated time against the changeover's
    standard time. A changeover with no standard time configured is left
    out of the efficiency figure rather than counted as perfect.
    """

    permission_classes = [IsAuthenticated]

    GROUPINGS = {
        'operator': ('operator__employee_id', 'operator__name'),
        'shift': ('shift',),
        'date': ('production_date',),
        'machine': ('changeover__batch',),
    }

    def get(self, request):
        params = request.query_params
        today = datetime.datetime.now().date()
        start = _parse_date(params.get('start_date'),
                            today - datetime.timedelta(days=30))
        end = _parse_date(params.get('end_date'), today)

        group_by = params.get('group_by', 'operator')
        if group_by not in self.GROUPINGS:
            return Response(
                {'error': f"group_by must be one of "
                          f"{', '.join(sorted(self.GROUPINGS))}."},
                status=status.HTTP_400_BAD_REQUEST)

        allocations = SetupAllocation.objects.filter(
            production_date__gte=start, production_date__lte=end)

        shift = params.get('shift')
        if shift:
            allocations = allocations.filter(shift=shift.upper())

        # Substring, to match the dashboard's own recipe filter, which
        # does `current_recipe.includes(selected)` in the browser.
        recipe = params.get('recipe')
        if recipe:
            allocations = allocations.filter(
                changeover__current_recipe__icontains=recipe)

        fields = self.GROUPINGS[group_by]
        rows = list(
            allocations.values(*fields)
            .annotate(
                setups=Count('changeover', distinct=True),
                allocated_minutes=Sum('allocated_minutes'),
                gross_minutes=Sum('gross_minutes'),
                interrupted_minutes=Sum('interrupted_minutes'),
            )
            .order_by('-allocated_minutes')
        )

        standard = self._standard_by_group(allocations, fields)

        for row in rows:
            # Captured before the keys are renamed for display.
            key = tuple(row[field] for field in fields)

            if group_by == 'operator':
                row['employee_id'] = row.pop('operator__employee_id') or None
                row['label'] = row.pop('operator__name') or 'Unattributed'
            elif group_by == 'machine':
                row['label'] = f"Batch {row.pop('changeover__batch')}"
            else:
                key = fields[0]
                value = row.pop(key)
                row['label'] = str(value)
            setups = row['setups'] or 0
            # Setup time per changeover, net of any approved delay. This
            # is what the standard is compared against: a standard is a
            # target for setup work, and a breakdown is not setup work,
            # so charging somebody's target with a compound shortage
            # would make the comparison meaningless.
            row['average_minutes'] = (
                round(row['allocated_minutes'] / setups, 2) if setups else 0.0)
            for field in ('allocated_minutes', 'gross_minutes',
                          'interrupted_minutes'):
                row[field] = round(row[field] or 0.0, 2)

            row.update(self._standard_figures(
                standard.get(key), row['average_minutes']))

        totals = allocations.aggregate(
            allocated_minutes=Sum('allocated_minutes'),
            gross_minutes=Sum('gross_minutes'),
            interrupted_minutes=Sum('interrupted_minutes'),
            setups=Count('changeover', distinct=True),
        )
        for field in ('allocated_minutes', 'gross_minutes',
                      'interrupted_minutes'):
            totals[field] = round(totals[field] or 0.0, 2)

        # Across every changeover in range, again counted once each.
        overall = self._standard_by_group(allocations, ())
        total_setups = totals['setups'] or 0
        totals['average_minutes'] = (
            round(totals['allocated_minutes'] / total_setups, 2)
            if total_setups else 0.0)
        totals.update(self._standard_figures(
            overall.get(()), totals['average_minutes']))

        unattributed = round(
            allocations.filter(operator__isnull=True)
            .aggregate(total=Sum('allocated_minutes'))['total'] or 0.0, 2)

        return Response({
            'group_by': group_by,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'rows': rows,
            'totals': totals,
            'unattributed_minutes': unattributed,
        })

    @staticmethod
    def _standard_by_group(allocations, fields):
        """Standard time per group, counted once per changeover.

        Standard time belongs to the changeover, not to a segment of it.
        An operator who worked both sides of a shift change has two
        allocation rows for one changeover, so summing the standard
        across rows would charge them for it twice -- and the comparison
        the whole column exists for would be nonsense.

        Changeovers with no standard configured are left out rather than
        counted as zero, which would drag the average down and read as
        though somebody beat a target that was never set.
        """
        buckets = {}
        # order_by() clears Meta.ordering first. SetupAllocation orders by
        # segment_start, and Django adds ordering columns to the SELECT --
        # which defeats DISTINCT, so every segment came back separately
        # and a two-segment changeover counted its standard twice.
        pairs = allocations.order_by().values(
            *fields, 'changeover_id', 'changeover__standard_time',
        ).distinct()

        for pair in pairs:
            key = tuple(pair[field] for field in fields)
            bucket = buckets.setdefault(key, {'minutes': 0.0, 'count': 0})
            value = pair['changeover__standard_time']
            if value is not None:
                bucket['minutes'] += float(value)
                bucket['count'] += 1
        return buckets

    @staticmethod
    def _standard_figures(bucket, average_actual):
        """How the group's actual time compares with its standard.

        Nulls rather than zeros where no standard is configured: "no
        target" and "a target of nothing" are different answers, and the
        client should be able to tell them apart.
        """
        if not bucket or not bucket['count']:
            return {
                'standard_minutes': None,
                'changeovers_with_standard': 0,
                'average_standard_minutes': None,
                'variance_minutes': None,
            }

        average_standard = bucket['minutes'] / bucket['count']
        return {
            'standard_minutes': round(bucket['minutes'], 2),
            'changeovers_with_standard': bucket['count'],
            'average_standard_minutes': round(average_standard, 2),
            # Positive means over the target, which is the direction a
            # plant reads instinctively.
            'variance_minutes': round(average_actual - average_standard, 2),
        }


class ChangeoverSetupDetailAPIView(APIView):
    """One changeover's split, with the interruptions behind it.

    Keyed on the primary key, like changeover/update/<pk>/ -- that is the
    id the dashboard already holds for a row, and batch is a float, which
    makes for a URL like /setup-time/99100.0/.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            changeover = ChangeoverSummary.objects.get(pk=pk)
        except ChangeoverSummary.DoesNotExist:
            return Response({'error': 'No such changeover.'},
                            status=status.HTTP_404_NOT_FOUND)

        allocations = changeover.allocations.select_related('operator')
        # Matched by overlap -- a stoppage has no changeover of its own.
        stoppages = stoppages_overlapping(
            changeover.setup_start_timestamp,
            changeover.setup_complete_timestamp,
        ) if changeover.setup_start_timestamp and \
             changeover.setup_complete_timestamp else LineStoppage.objects.none()

        return Response({
            'id': changeover.pk,
            'batch': changeover.batch,
            'shift': changeover.shift,
            'production_date': changeover.production_date,
            'setup_start': changeover.setup_start_timestamp,
            'setup_complete': changeover.setup_complete_timestamp,
            'setup_time_actual': changeover.setup_time_actual,
            'standard_time': changeover.standard_time,
            'allocations': SetupAllocationSerializer(allocations, many=True).data,
            'interruptions': LineStoppageSerializer(stoppages, many=True).data,
        })


class InterruptionReviewAPIView(APIView):
    """Approve or reject a recorded interruption. Managers only.

    An interruption subtracts minutes from an operator's setup figure, so
    it is a claim about that operator's performance -- the same kind of
    claim as an overshoot reason, and it goes to the same people. Until
    it is approved it changes no number.
    """

    permission_classes = [CanApproveRequests]

    def post(self, request, pk):
        try:
            interruption = SetupInterruption.objects.select_related(
                'changeover').get(pk=pk)
        except SetupInterruption.DoesNotExist:
            return Response({'error': 'No such interruption.'},
                            status=status.HTTP_404_NOT_FOUND)

        serializer = InterruptionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']

        interruption.status = (
            SetupInterruption.Status.APPROVED if action == 'approve'
            else SetupInterruption.Status.REJECTED)
        interruption.reviewed_by = request.user
        interruption.reviewed_at = datetime.datetime.now()
        interruption.review_note = serializer.validated_data.get('note', '')
        interruption.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_note'])

        # Approving makes the minutes stop counting; rejecting gives them
        # back. Either way the split has to be recomputed now rather than
        # at the next pipeline pass.
        rebuild_allocations(interruption.changeover)

        return Response(
            SetupInterruptionSerializer(interruption).data,
            status=status.HTTP_200_OK)


class SetupAttributionAPIView(APIView):
    """Say who worked a stretch of setup that nobody was signed in for.

    Shift incharges and managers only: it is a judgement about who was on
    the line, which is exactly what a supervisor is there to know.

    POST with segment_start and employee_id to assign, DELETE with
    segment_start to take it back.
    """

    permission_classes = [CanRecordOvershootReason]

    def _changeover(self, pk):
        try:
            return ChangeoverSummary.objects.get(pk=pk)
        except ChangeoverSummary.DoesNotExist:
            return None

    def post(self, request, pk):
        changeover = self._changeover(pk)
        if changeover is None:
            return Response({'error': 'No such changeover.'},
                            status=status.HTTP_404_NOT_FOUND)

        payload = dict(request.data)
        payload['changeover'] = changeover.pk
        serializer = SetupAttributionSerializer(data=payload)
        serializer.is_valid(raise_exception=True)

        operator = serializer.validated_data['employee_id']
        segment_start = serializer.validated_data['segment_start']

        SetupAttribution.objects.update_or_create(
            changeover=changeover,
            segment_start=segment_start,
            defaults={
                'operator': operator,
                'assigned_by': request.user,
                'note': serializer.validated_data.get('note', ''),
            },
        )
        rebuild_allocations(changeover)

        return Response(
            {'message': f'{segment_start:%H:%M} assigned to '
                        f'{operator.name} ({operator.employee_id}).'},
            status=status.HTTP_200_OK)

    def delete(self, request, pk):
        changeover = self._changeover(pk)
        if changeover is None:
            return Response({'error': 'No such changeover.'},
                            status=status.HTTP_404_NOT_FOUND)

        raw = request.query_params.get('segment_start')
        if not raw:
            return Response({'error': 'Which segment? Pass segment_start.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            # Parsed rather than handed to the ORM as a string: the
            # column is a datetime, and a malformed value would surface
            # as a 500 instead of a complaint about the input.
            segment_start = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return Response(
                {'error': 'segment_start must look like 2026-11-08T08:00:00.'},
                status=status.HTTP_400_BAD_REQUEST)

        removed, _ = SetupAttribution.objects.filter(
            changeover=changeover, segment_start=segment_start).delete()
        if removed:
            rebuild_allocations(changeover)
        return Response({'removed': bool(removed)},
                        status=status.HTTP_200_OK)


class AssignableOperatorsAPIView(APIView):
    """Operators a setup segment can be assigned to.

    A thin list of its own rather than reusing the user administration
    endpoint, which is managers-only -- a shift incharge needs to assign
    setup time without being able to administer accounts.
    """

    permission_classes = [CanRecordOvershootReason]

    def get(self, request):
        operators = User.objects.filter(
            role=User.Role.OPERATOR,
        ).exclude(status=User.Status.RETIRED).order_by('name')
        return Response([
            {'employee_id': person.employee_id, 'name': person.name}
            for person in operators
        ])
