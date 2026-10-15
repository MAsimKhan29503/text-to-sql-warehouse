import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg

from t2s.config import get_settings
from t2s.schema_notes import COLUMN_NOTES, JOIN_HINTS, TABLE_NOTES

SCHEMA = "analytics_marts"
CACHE_FILE = Path(__file__).resolve().parent.parent / ".cache" / "schema_context.json"
CACHE_MAX_AGE_SECONDS = 3600   # the pipeline loads hourly, so an hour-old picture is fine
CACHE_VERSION = 1              # bump when the layout of the context text changes
LOW_VOLUME_RATIO = 0.5         # a day below 50% of a typical day counts as incomplete


@dataclass
class SchemaContext:
    text: str                      # what goes into the prompt
    first_day: str
    last_day: str
    last_complete_day: str | None
    missing_days: list[str]
    low_days: list[str]
    built_at: float
    notes_hash: str


def _human(n: int) -> str:
    if n < 0:
        return "unknown size"
    if n >= 1_000_000:
        return f"~{n / 1_000_000:.1f}M rows"
    if n >= 1_000:
        return f"~{n / 1_000:.0f}K rows"
    return f"{n} rows"


def analyze_days(day_totals: dict[date, int]) -> tuple[list[date], list[date], date | None]:
    """Return (missing days, low-volume days, last complete day)."""
    if not day_totals:
        return [], [], None
    days = sorted(day_totals)
    values = sorted(day_totals.values())
    typical = values[int(0.75 * (len(values) - 1))]  # robust against a few partial days
    low = [d for d in days if day_totals[d] < LOW_VOLUME_RATIO * typical]
    span = (days[-1] - days[0]).days + 1
    missing = [d for d in (days[0] + timedelta(n) for n in range(span)) if d not in day_totals]
    complete = [d for d in days if d not in low]
    return missing, low, (max(complete) if complete else None)


def _notes_hash() -> str:
    payload = repr((CACHE_VERSION, TABLE_NOTES, COLUMN_NOTES, JOIN_HINTS))
    return hashlib.md5(payload.encode()).hexdigest()


def _build(notes_hash: str) -> SchemaContext:
    s = get_settings()
    # Read through the READ-ONLY role: the model only ever sees what it may query.
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_ro_user, password=s.pg_ro_password, connect_timeout=5,
    ) as conn:
        cols = conn.execute(
            "select table_name, column_name, data_type from information_schema.columns "
            "where table_schema = %s order by table_name, ordinal_position",
            (SCHEMA,),
        ).fetchall()
        row_counts = dict(conn.execute(
            "select c.relname, c.reltuples::bigint from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = %s and c.relkind in ('r', 'p')",
            (SCHEMA,),
        ).fetchall())
        day_totals = {d: int(n) for d, n in conn.execute(
            "select event_date, sum(total_events)::bigint "
            "from analytics_marts.fct_daily_repo_activity group by event_date order by event_date"
        ).fetchall()}

    if not day_totals:
        raise RuntimeError("fct_daily_repo_activity is empty: nothing to describe.")
    missing, low, last_complete = analyze_days(day_totals)
    first_day, last_day = min(day_totals), max(day_totals)
    anchor = last_complete or last_day

    lines = [
        "DATABASE: PostgreSQL 16. Schema: analytics_marts (a star schema of GitHub public events).",
        f"DATA COVERS: {first_day} to {last_day}, but the data has gaps and incomplete days (see EVENTS PER DAY).",
        f"LAST COMPLETE DAY: {anchor}. Interpret relative dates such as 'last week' or "
        "'yesterday' relative to this day, not today's date.",
        "",
        "EVENTS PER DAY (all event types; LOW = probably incomplete, NO DATA = missing):",
    ]
    for n in range((last_day - first_day).days + 1):
        d = first_day + timedelta(n)
        if d in day_totals:
            flag = "  LOW" if d in low else ""
            lines.append(f"  {d}: {day_totals[d]:,}{flag}")
        else:
            lines.append(f"  {d}: NO DATA")
    lines.append("")

    tables: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in cols:
        tables.setdefault(table, []).append((column, dtype))
    for table, columns in tables.items():
        lines.append(f"TABLE {SCHEMA}.{table}  ({_human(row_counts.get(table, -1))})")
        if table in TABLE_NOTES:
            lines.append(f"  -- {TABLE_NOTES[table]}")
        for column, dtype in columns:
            note = COLUMN_NOTES.get((table, column))
            lines.append(f"  {column} {dtype}" + (f"  -- {note}" if note else ""))
        lines.append("")

    lines.append("JOINS (no foreign keys exist; these are the intended join keys):")
    lines.extend(f"  {j}" for j in JOIN_HINTS)

    return SchemaContext(
        text="\n".join(lines), first_day=str(first_day), last_day=str(last_day),
        last_complete_day=str(last_complete) if last_complete else None,
        missing_days=[str(d) for d in missing], low_days=[str(d) for d in low],
        built_at=time.time(), notes_hash=notes_hash,
    )


def load_schema_context(refresh: bool = False) -> SchemaContext:
    """Cached on disk for an hour; rebuilt early if the hand-written notes change."""
    notes_hash = _notes_hash()
    if not refresh and CACHE_FILE.exists():
        try:
            cached = SchemaContext(**json.loads(CACHE_FILE.read_text(encoding="utf-8")))
            if cached.notes_hash == notes_hash and time.time() - cached.built_at < CACHE_MAX_AGE_SECONDS:
                return cached
        except (ValueError, TypeError, OSError):
            pass  # unreadable or outdated cache: rebuild
    ctx = _build(notes_hash)
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(asdict(ctx)), encoding="utf-8")
    except OSError:
        pass  # a cache that cannot be written is not an error
    return ctx


def build_schema_context() -> str:
    return load_schema_context().text


def coverage_note(ctx: SchemaContext) -> str | None:
    """One honest sentence about gaps, for the UI to show next to results."""
    parts = []
    if ctx.missing_days:
        parts.append("no data on " + ", ".join(ctx.missing_days))
    if ctx.low_days:
        parts.append("incomplete (low volume) on " + ", ".join(ctx.low_days))
    if not parts:
        return None
    return (f"Data note: relative dates count from the last complete day, {ctx.last_complete_day}. "
            + "; ".join(parts) + ". Results that include those days may be undercounted.")


if __name__ == "__main__":
    ctx = load_schema_context(refresh="--refresh" in sys.argv)
    print(ctx.text)
    print(f"\n[{len(ctx.text):,} characters]")
    note = coverage_note(ctx)
    if note:
        print(f"\n{note}")