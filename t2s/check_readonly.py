import sys
import time

import psycopg
from psycopg import errors, sql

from t2s.config import get_settings

TABLES = ["dim_repo", "dim_actor", "fct_events", "fct_daily_repo_activity"]

# (label, statement, simulate_bypass, error we EXPECT)
BLOCKED_BY_READ_ONLY = [
    ("INSERT", "insert into analytics_marts.dim_repo (repo_id) values (-1)"),
    ("DELETE", "delete from analytics_marts.fct_events where false"),
    ("DROP TABLE", "drop table analytics_marts.dim_repo"),
    ("CREATE TABLE", "create table analytics_marts.evil (id int)"),
    ("CREATE TEMP TABLE", "create temp table evil (id int)"),
]
BLOCKED_BY_PRIVILEGES = [
    ("INSERT", "insert into analytics_marts.dim_repo (repo_id) values (-1)"),
    ("UPDATE", "update analytics_marts.dim_repo set repo_name = repo_name where false"),
    ("DELETE", "delete from analytics_marts.fct_events where false"),
    ("TRUNCATE", "truncate analytics_marts.fct_events"),
    ("DROP TABLE", "drop table analytics_marts.dim_repo"),
    ("CREATE TABLE", "create table analytics_marts.evil (id int)"),
]


def expect_error(conn, label, stmt, expected, bypass=False) -> bool:
    try:
        if bypass:
            conn.execute("set transaction read write")
        conn.execute(stmt)
    except expected:
        print(f"  PASS {label}: blocked ({expected.__name__})")
        return True
    except Exception as e:
        print(f"  FAIL {label}: wrong error {type(e).__name__}: {e}")
        return False
    else:
        print(f"  FAIL {label}: statement was ALLOWED")
        return False
    finally:
        conn.rollback()  # never keep anything from a test


def check_reads(conn) -> bool:
    print("[1] Reads should work")
    ok = True
    for t in TABLES:
        try:
            conn.execute(sql.SQL("select * from analytics_marts.{} limit 1").format(sql.Identifier(t)))
            print(f"  PASS can read {t}")
        except Exception as e:
            print(f"  FAIL cannot read {t}: {e}")
            ok = False
        finally:
            conn.rollback()
    start = time.perf_counter()
    n = conn.execute("select count(*) from analytics_marts.fct_events").fetchone()[0]
    print(f"  PASS count(*) on fct_events = {n:,} ({time.perf_counter() - start:.1f}s)")
    conn.rollback()
    return ok


def check_other_schemas(conn) -> bool:
    print("[4] Other schemas should be off limits")
    row = conn.execute(
        "select schemaname, tablename from pg_catalog.pg_tables "
        "where schemaname not in ('pg_catalog', 'information_schema', 'analytics_marts') limit 1"
    ).fetchone()
    conn.rollback()
    if not row:
        print("  SKIP no tables outside analytics_marts")
        return True
    stmt = sql.SQL("select 1 from {}.{} limit 1").format(sql.Identifier(row[0]), sql.Identifier(row[1]))
    return expect_error(conn, f"read {row[0]}.{row[1]}", stmt, errors.InsufficientPrivilege)


def check_timeout(conn) -> bool:
    print("[5] Statement timeout should cancel slow queries")
    shown = conn.execute("show statement_timeout").fetchone()[0]
    conn.rollback()
    start = time.perf_counter()
    try:
        conn.execute("select pg_sleep(60)")
    except errors.QueryCanceled:
        print(f"  PASS statement_timeout={shown}: cancelled after {time.perf_counter() - start:.1f}s")
        return True
    except Exception as e:
        print(f"  FAIL wrong error {type(e).__name__}: {e}")
        return False
    finally:
        conn.rollback()
    print("  FAIL query ran the full 60s")
    return False


def main() -> int:
    s = get_settings()
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_ro_user, password=s.pg_ro_password, connect_timeout=5,
    ) as conn:
        who = conn.execute("select current_user").fetchone()[0]
        conn.rollback()
        print(f"Connected as: {who}\n")

        results = [check_reads(conn)]

        print("\n[2] Layer 1: sessions are read-only by default")
        results += [expect_error(conn, l, q, errors.ReadOnlySqlTransaction) for l, q in BLOCKED_BY_READ_ONLY]

        print("\n[3] Layer 2: permissions hold even if read-only were bypassed")
        results += [
            expect_error(conn, l, q, errors.InsufficientPrivilege, bypass=True)
            for l, q in BLOCKED_BY_PRIVILEGES
        ]

        print()
        results.append(check_other_schemas(conn))
        print()
        results.append(check_timeout(conn))

    print("\nALL CHECKS PASSED" if all(results) else "\nSOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())