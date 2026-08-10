# data_processor/signals.py

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import ChangeoverSummary
from reports.tasks import send_critical_overshoot_alert


@receiver(post_save, sender=ChangeoverSummary)
def check_for_critical_overshoot(sender, instance, created, **kwargs):
    """
    Triggered right after a ChangeoverSummary is saved.
    Sends a Celery alert ONLY when the record is newly created (not updated)
    and the setup time is more than 2x the standard time.
    """

    # Run this only for newly created objects
    if not created:
        return

    # Ensure both values exist to avoid NoneType math errors
    if not (instance.setup_time_actual and instance.standard_time):
        return

    # 2× overshoot check
    is_critical = instance.setup_time_actual > (instance.standard_time * 2)
    if not is_critical:
        return

    # Schedule the Celery task AFTER DB commit (safe timing)
    def _enqueue_alert():
        print(f"CRITICAL Overshoot (>2x) detected for Batch {instance.batch}. Triggering task.")
        send_critical_overshoot_alert.delay(instance.id)

    transaction.on_commit(_enqueue_alert)
