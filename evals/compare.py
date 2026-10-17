"""Decide whether the model's result set answers the same question as the reference result set."""
from datetime import date, datetime
from decimal import Decimal


def normalize(value):
    """Make values from different SQL formulations comparable (Decimal vs int, dates, spacing)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), 4)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value).strip()


def _sort_key(item):
    values = item if isinstance(item, tuple) else (item,)
    return tuple((v is None, str(v)) for v in values)


def _same(a: list, b: list, ordered: bool) -> bool:
    return a == b if ordered else sorted(a, key=_sort_key) == sorted(b, key=_sort_key)


def results_match(model_rows: list[tuple], reference_rows: list[tuple], ordered: bool) -> tuple[bool, str]:
    """Compare two result sets, ignoring column names.

    Exact match first. Otherwise accept the same values in a different column order, or the
    reference columns plus extra columns. A model that leaves out a reference column is wrong.
    """
    model = [tuple(normalize(v) for v in row) for row in model_rows]
    ref = [tuple(normalize(v) for v in row) for row in reference_rows]

    if len(model) != len(ref):
        return False, f"row count differs: expected {len(ref)}, got {len(model)}"
    if not ref:
        return True, "both empty"
    if _same(model, ref, ordered):
        return True, "exact match"

    model_cols = [[row[i] for row in model] for i in range(len(model[0]))]
    ref_cols = [[row[i] for row in ref] for i in range(len(ref[0]))]
    used: set[int] = set()
    for ref_col in ref_cols:
        found = next(
            (j for j, col in enumerate(model_cols) if j not in used and _same(col, ref_col, ordered)),
            None,
        )
        if found is None:
            return False, "values differ"
        used.add(found)
    return True, "same values (columns reordered or extra columns)"