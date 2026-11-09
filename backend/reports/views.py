# reports/views.py

import io
import datetime
from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from rest_framework.decorators import (
    api_view,
    permission_classes,
    renderer_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer


class BinaryFileRenderer(BaseRenderer):
    """Lets DRF's content negotiation through for a file download.

    @api_view brings content negotiation with it, and DRF's default
    renderers only offer application/json and text/html. This view returns a
    PDF, so a client asking for `Accept: application/pdf` -- which the
    dashboard does -- matched no renderer and got 406 Not Acceptable before
    the view body ever ran.

    Declaring `*/*` makes negotiation always succeed. The view returns a
    plain HttpResponse, so nothing is actually rendered through this class;
    it exists to stop negotiation refusing the request.
    """

    media_type = '*/*'
    format = 'pdf'
    charset = None
    render_style = 'binary'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data

# PDF & Charting Libs
import matplotlib.pyplot as plt
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader

# --- IMPORT YOUR CORRECT MODEL ---
# Import the ChangeoverSummary model from your data_processor app
from data_processor.models import ChangeoverSummary


# --- Chart Generation (No changes needed here) ---
def generate_kpi_chart(kpis):
    """Generates a bar chart for Setup vs Standard Time."""
    try:
        plt.switch_backend('Agg')
        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(4, 2.5))

        times = ['Avg. Setup Time', 'Avg. Standard Time']
        values = [kpis.get('avg_setup_time', 0), kpis.get('avg_standard_time', 0)]

        bars = ax.bar(times, values, color=['#d9534f', '#5cb85c'])
        ax.set_title('Average Setup vs. Standard Time (min)', fontsize=10)
        ax.set_ylabel('Minutes', fontsize=8)

        for bar in bars:
            yval = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2.0, yval, f'{yval:.1f}', va='bottom', ha='center', fontsize=8)

        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='PNG', dpi=150)
        buf.seek(0)
        plt.close(fig)
        return buf
    except Exception as e:
        print(f"Error generating chart: {e}")
        return None


# --- Main PDF View (With Corrected Field Names) ---
@api_view(['GET'])
@permission_classes([IsAuthenticated])
@renderer_classes([BinaryFileRenderer])
def download_summary_report(request):
    """
    A Django view to generate and serve the PDF report.
    Accepts 'start_date' and 'end_date' query parameters.
    """

    # --- 1. Get and Validate Date Range ---
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    shift = request.GET.get('shift')

    # settings.USE_TZ is False, so this project stores and works in naive
    # local wall-clock time. timezone.now() therefore returns a naive
    # datetime, and timezone.localtime() raises ValueError on one -- which
    # made every request to this endpoint fail, since the call sits above
    # the parameter check below and ran unconditionally.
    now_local = datetime.datetime.now()
    end_date = now_local.date()
    start_date = end_date - datetime.timedelta(days=1)
    date_format = '%Y-%m-%d'

    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, date_format).date()
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, date_format).date()
        except ValueError:
            pass

    # --- 2. Fetch data using the same day-window logic as stats/export ---
    # Naive bounds, matching how the project stores timestamps (USE_TZ=False).
    # These were previously wrapped in timezone.make_aware(), which was a
    # no-op: recipe_change_time is a LocalTimeDateTimeField, and its
    # get_db_prep_value strips the tzinfo straight back off before the query.
    from_dt_fallback = datetime.datetime.combine(start_date, datetime.time(7, 0))
    to_dt_fallback = datetime.datetime.combine(
        end_date + datetime.timedelta(days=1), datetime.time(7, 0))

    summaries = ChangeoverSummary.objects.filter(
        recipe_change_time__gte=from_dt_fallback,
        recipe_change_time__lt=to_dt_fallback,
    )

    if shift:
        shift = shift.upper()
        if shift in ['A', 'B', 'C']:
            if shift == 'A':
                fallback_filter = Q(recipe_change_time__time__range=('07:00', '15:00'))
            elif shift == 'B':
                fallback_filter = Q(recipe_change_time__time__range=('15:00', '23:00'))
            else:
                fallback_filter = Q(recipe_change_time__time__gte='23:00') | Q(recipe_change_time__time__lt='07:00')

            summaries = summaries.filter(Q(shift=shift) | (Q(shift__isnull=True) & fallback_filter))

    summaries = summaries.order_by('-recipe_change_time')

    if not summaries.exists():
        msg = f"No summary data found for the period {start_date.strftime(date_format)} to {end_date.strftime(date_format)}."
        return JsonResponse({"error": msg}, status=200)

    # --- 3. Calculate KPIs (Using corrected field names) ---
    kpis = {"total_setups": 0, "total_setup_time": 0, "total_standard_time": 0, "total_ramp_up_loss": 0,
            "total_ramp_down_loss": 0, }

    for s in summaries:
        kpis["total_setups"] += 1
        kpis["total_setup_time"] += s.setup_time_actual or 0  # CORRECTED
        kpis["total_standard_time"] += s.standard_time or 0  # CORRECTED
        kpis["total_ramp_up_loss"] += s.ramp_up_time_loss or 0  # CORRECTED
        kpis["total_ramp_down_loss"] += s.ramp_down_time_loss or 0  # CORRECTED

    if kpis["total_setups"] > 0:
        kpis["avg_setup_time"] = kpis["total_setup_time"] / kpis["total_setups"]
        kpis["avg_standard_time"] = kpis["total_standard_time"] / kpis["total_setups"]
        kpis["avg_efficiency"] = (kpis["total_standard_time"] / kpis["total_setup_time"]) * 100 if kpis[
                                                                                                       "total_setup_time"] > 0 else 0

    kpis["total_time_loss"] = kpis["total_ramp_up_loss"] + kpis["total_ramp_down_loss"]

    # 4. Generate Chart
    chart_buffer = generate_kpi_chart(kpis)

    # 5. Create PDF Document
    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=landscape(letter))
    width, height = landscape(letter)

    # --- Draw PDF Content ---
    title_start = start_date.strftime(date_format)
    title_end = end_date.strftime(date_format)
    report_title = f"4RC Changeover Report ({title_start} to {title_end})"

    c.setFont("Helvetica-Bold", 16)
    c.drawString(1 * inch, height - 0.75 * inch, report_title)

    # KPI Summary Section (No changes needed)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1 * inch, height - 1.25 * inch, "KPI Summary")
    c.setFont("Helvetica", 10)
    y_kpi = height - 1.6 * inch
    c.drawString(1 * inch, y_kpi, f"Average Efficiency:")
    c.drawString(1 * inch, y_kpi - 20, f"Average Setup Time:")
    c.drawString(1 * inch, y_kpi - 40, f"Total Time Loss (Ramp):")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2.5 * inch, y_kpi, f"{kpis.get('avg_efficiency', 0):.1f} %")
    c.drawString(2.5 * inch, y_kpi - 20, f"{kpis.get('avg_setup_time', 0):.1f} min")
    c.drawString(2.5 * inch, y_kpi - 40, f"{kpis.get('total_time_loss', 0):.1f} min")

    if chart_buffer:
        c.drawImage(ImageReader(chart_buffer), 4.5 * inch, height - 3.75 * inch, width=4 * inch, height=2.5 * inch)

    # --- Data Table Section (Using corrected field names) ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1 * inch, height - 4.25 * inch, "Changeover Details")

    c.setFont("Helvetica-Bold", 8)
    y_table = height - 4.6 * inch
    c.drawString(0.7 * inch, y_table, "Batch")
    c.drawString(1.2 * inch, y_table, "Previous Recipe")
    c.drawString(2.7 * inch, y_table, "Current Recipe")
    c.drawString(4.2 * inch, y_table, "Changeover Type")
    c.drawString(6.5 * inch, y_table, "Setup Time")
    c.drawString(7.5 * inch, y_table, "Std. Time")
    c.drawString(8.5 * inch, y_table, "Ramp Up Loss")
    c.drawString(9.5 * inch, y_table, "Ramp Down Loss")
    c.line(0.5 * inch, y_table - 5, width - 0.5 * inch, y_table - 5)

    c.setFont("Helvetica", 7)
    y_pos = y_table - 20
    for row in summaries:
        if y_pos < 0.5 * inch: break

        c.drawString(0.7 * inch, y_pos, str(row.batch))  # CORRECTED
        c.drawString(1.2 * inch, y_pos, str(row.previous_recipe))  # CORRECTED
        c.drawString(2.7 * inch, y_pos, str(row.current_recipe))  # CORRECTED
        c.drawString(4.2 * inch, y_pos, str(row.change_over_type))  # CORRECTED
        c.drawString(6.5 * inch, y_pos, f"{row.setup_time_actual or 0:.1f}")  # CORRECTED
        c.drawString(7.5 * inch, y_pos, f"{row.standard_time or 0:.1f}")  # CORRECTED
        c.drawString(8.5 * inch, y_pos, f"{row.ramp_up_time_loss or 0:.1f}")  # CORRECTED
        c.drawString(9.5 * inch, y_pos, f"{row.ramp_down_time_loss or 0:.1f}")  # CORRECTED
        y_pos -= 15

    # --- Finalize PDF ---
    c.save()
    pdf_buffer.seek(0)

    # 6. Send Response
    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'inline; filename="4RC_Summary_Report_{start_date.strftime(date_format)}_to_{end_date.strftime(date_format)}.pdf"'
    )

    return response