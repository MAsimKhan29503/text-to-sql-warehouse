import time
from dataclasses import dataclass, field

import psycopg
from psycopg import errors

from t2s.config import get_settings

MAX_FETCH = 500  # safety net; the guardrails already force LIMIT 100


@dataclass
class QueryResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None  # "timeout" or "database"
    milliseconds: int = 0


def _clean_error(e: psycopg.Error) -> str:
    diag = e.diag
    message = diag.message_primary or str(e).strip()
    if diag.message_hint:
        message += f" Hint: {diag.message_hint}"
    return message


def run_query(sql: str) -> QueryResult:
    """Run ONE already-validated query through the read-only role."""
    s = get_settings()
    start = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        with psycopg.connect(
            host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
            user=s.pg_ro_user, password=s.pg_ro_password, connect_timeout=5,
        ) as conn:
            cur = conn.execute(sql)
            columns = [c.name for c in cur.description]
            rows = cur.fetchmany(MAX_FETCH)
        return QueryResult(True, columns, rows, milliseconds=elapsed())
    except errors.QueryCanceled:
        return QueryResult(
            False, error_kind="timeout", milliseconds=elapsed(),
            error="The query took too long and was cancelled. Aggregate more, or filter to fewer days.",
        )
    except psycopg.OperationalError as e:  # server down, refused connection, too many connections
        return QueryResult(
            False, error_kind="unavailable", milliseconds=elapsed(),
            error=f"The database is unavailable: {str(e).strip()}",
        )
    except psycopg.Error as e:  # the SQL itself was wrong (unknown column, bad syntax, ...)
        return QueryResult(False, error=_clean_error(e), error_kind="database", milliseconds=elapsed())