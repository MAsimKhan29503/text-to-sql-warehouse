import psycopg

from t2s.config import get_settings
from t2s.schema_notes import COLUMN_NOTES, JOIN_HINTS, TABLE_NOTES

SCHEMA = "analytics_marts"


def _human(n: int) -> str:
    if n < 0:
        return "unknown size"
    if n >= 1_000_000:
        return f"~{n / 1_000_000:.1f}M rows"
    if n >= 1_000:
        return f"~{n / 1_000:.0f}K rows"
    return f"{n} rows"


def build_schema_context() -> str:
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
        first_day, last_day = conn.execute(
            "select min(event_date), max(event_date) from analytics_marts.fct_daily_repo_activity"
        ).fetchone()

    tables: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in cols:
        tables.setdefault(table, []).append((column, dtype))

    lines = [
        "DATABASE: PostgreSQL 16. Schema: analytics_marts (a star schema of GitHub public events).",
        f"DATA COVERS: {first_day} to {last_day}. Interpret relative dates such as "
        "'last week' relative to the last day of data, not today's date.",
        "",
    ]
    for table, columns in tables.items():
        size = _human(row_counts.get(table, -1))
        lines.append(f"TABLE {SCHEMA}.{table}  ({size})")
        if table in TABLE_NOTES:
            lines.append(f"  -- {TABLE_NOTES[table]}")
        for column, dtype in columns:
            note = COLUMN_NOTES.get((table, column))
            suffix = f"  -- {note}" if note else ""
            lines.append(f"  {column} {dtype}{suffix}")
        lines.append("")

    lines.append("JOINS (no foreign keys exist; these are the intended join keys):")
    lines.extend(f"  {j}" for j in JOIN_HINTS)
    return "\n".join(lines)


if __name__ == "__main__":
    text = build_schema_context()
    print(text)
    print(f"\n[{len(text):,} characters]")