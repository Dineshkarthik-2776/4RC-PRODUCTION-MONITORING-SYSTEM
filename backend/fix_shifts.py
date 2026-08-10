import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from data_processor.models import ChangeoverSummary

def get_shift(dt):
    h = dt.hour
    if 7 <= h < 15:
        return 'A'
    elif 15 <= h < 23:
        return 'B'
    else:
        return 'C'

records = ChangeoverSummary.objects.exclude(recipe_change_time__isnull=True)
total = records.count()
updated = 0

print(f"Total records to process: {total}")

for r in records.iterator():
    correct_shift = get_shift(r.recipe_change_time)
    if r.shift != correct_shift:
        r.shift = correct_shift
        r.save(update_fields=['shift'])
        updated += 1

print(f"Done. {updated} records updated out of {total}.")
