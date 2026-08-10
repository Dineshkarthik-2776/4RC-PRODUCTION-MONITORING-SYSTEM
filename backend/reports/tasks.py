# reports/tasks.py

import io
import datetime
from celery import shared_task
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

# Import the model from your other app
from data_processor.models import ChangeoverSummary


@shared_task
def send_critical_overshoot_alert(summary_id):
    """
    Fetches a ChangeoverSummary and sends a formatted KPI email for a
    CRITICAL (e.g., >2x) overshoot.
    """
    try:
        # Get the specific summary object that triggered this
        summary = ChangeoverSummary.objects.get(id=summary_id)
    except ChangeoverSummary.DoesNotExist:
        print(f"Critical Alert Task: Summary ID {summary_id} not found.")
        return

    # Calculate the difference for the email body
    time_diff = (summary.setup_time_actual or 0) - (summary.standard_time or 0)

    # Format the "Option 2" KPI Summary Email
    subject = f"CRITICAL Alert: Batch {summary.batch} - Setup Time > 2x Standard"
    message = f"""
    A critical setup time overshoot has been recorded.
    The actual time taken was more than double the standard time.

    Event Details:
    - Batch: {summary.batch}
    - Changeover: {summary.previous_recipe} to {summary.current_recipe}
    - Reason: {summary.overshoot.category if summary.overshoot else 'N/A'} - {summary.overshoot.reason if summary.overshoot else 'N/A'}

    Impact:
    - Actual Time: {summary.setup_time_actual:.1f} min
    - Standard Time: {summary.standard_time:.1f} min
    - Difference: {time_diff:.1f} min over standard

    This is an automated critical alert.
    """

    # Send to your plant manager or engineering lead
    recipient_list = ['dineshkarthik2776@gmail.com']

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        recipient_list,
        fail_silently=False,
    )
    return f"Sent CRITICAL alert for Batch {summary.batch}"
