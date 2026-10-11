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
from user_authentication.permissions import (
    CanRecordOvershootReason,
    IsManager,
    IsManagerOrReadOnly,
)
from .models import RecipeMaster, ChangeoverSummary, StandardTimeMaster, RawLineData
from .tasks import process_changeover_data
from .serializers import (
    RecipeMasterSerializer,
    RecipeMasterCreateSerializer,
    ChangeoverDetailSerializer,
    ChangeoverUpdateSerializer,
    StandardTimeSerializer
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
    Managers only.
    """
    permission_classes = [IsManager]
    serializer_class = ChangeoverCorrectionRequestSerializer

    def get_queryset(self):
        return ChangeoverCorrectionRequest.objects.filter(status=ChangeoverCorrectionRequest.Status.PENDING)


class CorrectionRequestActionAPIView(APIView):
    """
    Approve or Reject a correction request.
    Managers only.
    """
    permission_classes = [IsManager]

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

