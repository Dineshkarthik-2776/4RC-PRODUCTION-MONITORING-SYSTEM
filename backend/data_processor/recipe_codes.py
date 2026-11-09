"""One place for recipe-code matching rules.

The same recipe reaches this system spelled several ways -- 'CPJ 120' from the
ERP, 'CPJ120' from the line sensors, 'cpj 120' from a manual upload. Matching
therefore happens on a normalised form.

Storage is the opposite: RecipeMaster keeps the human spelling, because it is
what operators recognise and what the dashboard's recipe dropdown is built
from. ChangeoverSummary adopts that same spelling (see _master_spelling in
tasks.py), so a dropdown value and a summary row compare equal.

So: NORMALISE TO MATCH, STORE THE HUMAN SPELLING. Both rules live here so they
cannot drift apart again.
"""

from data_processor.models import RecipeMaster


def normalize_recipe_code(value):
    """Collapse a recipe code to its match key: no spaces, upper case."""
    return str(value or '').strip().replace(' ', '').upper()


def find_master_by_code(value):
    """Return the RecipeMaster row for this code, whatever its spelling.

    RecipeMaster.recipe_code is the primary key and holds the human spelling,
    so an exact lookup misses 'CP034HI' when the stored row says 'CP 034 HI'.
    This matches on the normalised form instead.

    Returns None when no row matches.
    """
    normalized = normalize_recipe_code(value)
    if not normalized:
        return None

    # Exact hit first: the common case, and it avoids scanning the table.
    exact = RecipeMaster.objects.filter(recipe_code=value).first()
    if exact is not None:
        return exact

    for candidate in RecipeMaster.objects.all().only(
            'recipe_code', 'sap_code', 'target_speed', 'recipe_type'):
        if normalize_recipe_code(candidate.recipe_code) == normalized:
            return candidate
    return None


def upsert_master_recipe(value, defaults=None, update_existing=True):
    """Create or update the RecipeMaster row for this code.

    Matches on the normalised form, so a code differing only in spacing or
    case updates the existing row instead of creating a second master entry
    for the same recipe. An existing row KEEPS its own spelling -- the first
    spelling seen wins, and the ERP is normally what sets it.

    Returns (row, created).
    """
    defaults = dict(defaults or {})
    existing = find_master_by_code(value)

    if existing is None:
        normalized = normalize_recipe_code(value)
        if not normalized:
            return None, False
        return RecipeMaster.objects.create(
            recipe_code=str(value).strip(), **defaults), True

    if update_existing and defaults:
        changed = [f for f, v in defaults.items() if getattr(existing, f) != v]
        if changed:
            for field in changed:
                setattr(existing, field, defaults[field])
            existing.save(update_fields=changed)
    return existing, False
