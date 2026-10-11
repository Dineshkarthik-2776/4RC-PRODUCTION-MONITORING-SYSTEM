"""Rewrite ChangeoverSummary recipe codes to the spelling RecipeMaster holds.

Recipe codes are normalised for matching (spaces stripped, upper-cased), and
for a period the pipeline also STORED that normalised form. The UI builds its
recipe dropdown from RecipeMaster, which keeps the human spelling, so rows
written during that period cannot be matched by the filter -- it silently
returns nothing.

This command brings those rows back in line. It matches on the normalised
code, so it is correct whatever spelling a row currently has, and it is safe
to re-run: rows already holding the master spelling are left untouched.

    python manage.py backfill_recipe_spelling --dry-run
    python manage.py backfill_recipe_spelling
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from data_processor.models import ChangeoverSummary, RecipeMaster
from data_processor.tasks import _normalize_recipe_code

FIELDS = ('current_recipe', 'previous_recipe')


class Command(BaseCommand):
    help = 'Align ChangeoverSummary recipe codes with the RecipeMaster spelling.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing anything.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help='Rows per bulk_update call (default: 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        display_map = {
            _normalize_recipe_code(r.recipe_code): r.recipe_code
            for r in RecipeMaster.objects.all()
        }
        if not display_map:
            self.stderr.write(self.style.ERROR(
                'RecipeMaster is empty -- nothing to align against. Aborting.'))
            return

        self.stdout.write(f'Loaded {len(display_map)} recipes from RecipeMaster.')

        pending, unmatched, examples = [], set(), []

        for summary in ChangeoverSummary.objects.all().iterator(chunk_size=1000):
            changed = False
            for field in FIELDS:
                current = getattr(summary, field)
                if not current:
                    continue
                normalized = _normalize_recipe_code(current)
                target = display_map.get(normalized)
                if target is None:
                    unmatched.add(current)
                    continue
                if target != current:
                    setattr(summary, field, target)
                    changed = True
                    if len(examples) < 8:
                        examples.append(f'  id={summary.id} {field}: '
                                        f'{current!r} -> {target!r}')
            if changed:
                pending.append(summary)

        self.stdout.write('')
        for line in examples:
            self.stdout.write(line)
        if len(pending) > len(examples):
            self.stdout.write(f'  ... and {len(pending) - len(examples)} more rows')

        if unmatched:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f'{len(unmatched)} code(s) have no RecipeMaster row and were left '
                f'unchanged: {sorted(unmatched)[:10]}'))

        self.stdout.write('')
        if not pending:
            self.stdout.write(self.style.SUCCESS('Nothing to change.'))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN: {len(pending)} row(s) would be updated. '
                'Re-run without --dry-run to apply.'))
            return

        with transaction.atomic():
            ChangeoverSummary.objects.bulk_update(
                pending, list(FIELDS), batch_size=batch_size)

        self.stdout.write(self.style.SUCCESS(f'Updated {len(pending)} row(s).'))
