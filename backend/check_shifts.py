import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from data_processor.models import ChangeoverSummary

def expected_shift(dt):
    h = dt.hour
    if 7 <= h < 15:
        return 'A'
    elif 15 <= h < 23:
        return 'B'
    else:
        return 'C'

records = ChangeoverSummary.objects.exclude(recipe_change_time__isnull=True).order_by('-recipe_change_time')[:10]

print(f"{'ID':<8} {'Recipe Change Time':<28} {'Stored':^10} {'Expected':^10} {'Status'}")
print('-' * 70)

all_ok = True
for r in records:
    stored = r.shift
    expected = expected_shift(r.recipe_change_time)
    status = 'OK' if stored == expected else 'MISMATCH'
    if stored != expected:
        all_ok = False
    print(f"{r.id:<8} {str(r.recipe_change_time):<28} {stored:^10} {expected:^10} {status}")

print('-' * 70)
if all_ok:
    print("All 10 records have correct shifts.")
else:
    print("Some records have mismatched shifts. No changes were made.")
