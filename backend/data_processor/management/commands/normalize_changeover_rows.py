import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from data_processor.models import ChangeoverSummary


class Command(BaseCommand):
    help = "Normalize existing ChangeoverSummary timestamps and recompute shift/production_date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many rows would be updated without writing changes.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of rows to process per chunk.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        total = 0
        changed = 0

        last_id = 0

        while True:
            batch = list(
                ChangeoverSummary.objects.filter(id__gt=last_id)
                .order_by("id")[:batch_size]
            )
            if not batch:
                break

            for summary in batch:
                total += 1

                original_recipe_change_time = summary.recipe_change_time
                original_ramp_down = summary.ramp_down_timestamp
                original_setup_start = summary.setup_start_timestamp
                original_ramp_up = summary.ramp_up_timestamp
                original_setup_complete = summary.setup_complete_timestamp
                original_shift = summary.shift
                original_production_date = summary.production_date

                def normalize(value):
                    if value is None:
                        return None
                    if timezone.is_aware(value):
                        value = timezone.localtime(value)
                    return value.replace(tzinfo=None)

                recipe_change_time = normalize(original_recipe_change_time)
                ramp_down_timestamp = normalize(original_ramp_down)
                setup_start_timestamp = normalize(original_setup_start)
                ramp_up_timestamp = normalize(original_ramp_up)
                setup_complete_timestamp = normalize(original_setup_complete)

                shift = None
                production_date = None
                if recipe_change_time is not None:
                    hour = recipe_change_time.hour
                    if 7 <= hour < 15:
                        shift = "A"
                        production_date = recipe_change_time.date()
                    elif 15 <= hour < 23:
                        shift = "B"
                        production_date = recipe_change_time.date()
                    else:
                        shift = "C"
                        production_date = (
                            recipe_change_time - datetime.timedelta(days=1)
                        ).date() if 0 <= hour < 7 else recipe_change_time.date()

                fields_to_update = []
                if recipe_change_time != original_recipe_change_time:
                    summary.recipe_change_time = recipe_change_time
                    fields_to_update.append("recipe_change_time")
                if ramp_down_timestamp != original_ramp_down:
                    summary.ramp_down_timestamp = ramp_down_timestamp
                    fields_to_update.append("ramp_down_timestamp")
                if setup_start_timestamp != original_setup_start:
                    summary.setup_start_timestamp = setup_start_timestamp
                    fields_to_update.append("setup_start_timestamp")
                if ramp_up_timestamp != original_ramp_up:
                    summary.ramp_up_timestamp = ramp_up_timestamp
                    fields_to_update.append("ramp_up_timestamp")
                if setup_complete_timestamp != original_setup_complete:
                    summary.setup_complete_timestamp = setup_complete_timestamp
                    fields_to_update.append("setup_complete_timestamp")
                if shift != original_shift:
                    summary.shift = shift
                    fields_to_update.append("shift")
                if production_date != original_production_date:
                    summary.production_date = production_date
                    fields_to_update.append("production_date")

                if fields_to_update:
                    changed += 1
                    if not dry_run:
                        summary.save(update_fields=fields_to_update)

                last_id = summary.id

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run complete. {changed} of {total} rows would be updated."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backfill complete. Updated {changed} of {total} rows."
                )
            )
