import sys

import psycopg
from psycopg import errors

from t2s.check_readonly import expect_error
from t2s.config import get_settings
from t2s.query_log import INSERT

LOGGER_MUST_FAIL = [
    ("UPDATE a log row", "update text2sql.query_log set question = 'x'"),
    ("DELETE log rows", "delete from text2sql.query_log"),
    ("TRUNCATE the log", "truncate text2sql.query_log"),
    ("DROP the log", "drop table text2sql.query_log"),
    ("read the warehouse", "select * from analytics_marts.fct_events limit 1"),
    ("create a table", "create table text2sql.evil (id int)"),
]


def check_logger_can_append(conn) -> bool:
    try:
        conn.execute(INSERT, ("selftest", "self-test question", "ok", None, None, None, 0, 0.0, 0, 0, "none"))
        print("  PASS logger can INSERT (rolled back, nothing kept)")
        return True
    except Exception as e:
        print(f"  FAIL logger cannot insert: {type(e).__name__}: {e}")
        return False
    finally:
        conn.rollback()


def main() -> int:
    s = get_settings()
    results = []

    print("[1] The logger role: append-only")
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_log_user, password=s.pg_log_password, connect_timeout=5,
    ) as conn:
        results.append(check_logger_can_append(conn))
        for label, stmt in LOGGER_MUST_FAIL:
            results.append(expect_error(conn, label, stmt, errors.InsufficientPrivilege))

    print("\n[2] The read-only (LLM) role must not see the log")
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_ro_user, password=s.pg_ro_password, connect_timeout=5,
    ) as conn:
        results.append(expect_error(
            conn, "read query_log", "select * from text2sql.query_log limit 1", errors.InsufficientPrivilege
        ))

    print("\nALL CHECKS PASSED" if all(results) else "\nSOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())