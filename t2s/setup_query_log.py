import psycopg
from psycopg import sql

from t2s.config import get_settings

SCHEMA = "text2sql"

CREATE_TABLE = """
create table if not exists text2sql.query_log (
    id            bigint generated always as identity primary key,
    asked_at      timestamptz not null default now(),
    source        text not null default 'cli',
    question      text not null,
    status        text not null check (status in ('ok', 'declined', 'blocked', 'failed')),
    raw_sql       text,
    executed_sql  text,
    message       text,
    row_count     integer,
    llm_seconds   numeric(8, 2),
    db_ms         integer,
    tokens        integer,
    model         text,
    attempts      integer not null default 1
)
"""


def main() -> None:
    s = get_settings()
    role = sql.Identifier(s.pg_log_user)
    database = sql.Identifier(s.pg_db)

    # Connect as the OWNER account (admin script: the app never does this).
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_user, password=s.pg_password, connect_timeout=5,
    ) as conn:
        with conn.cursor() as cur:
            # 1) A separate schema keeps app data away from the analytics marts.
            cur.execute(f"create schema if not exists {SCHEMA}")
            cur.execute(CREATE_TABLE)
                          # Added in Step 8; this line upgrades a table created in Step 7.
            cur.execute(f"alter table {SCHEMA}.query_log add column if not exists attempts integer not null default 1")
            cur.execute(f"create index if not exists idx_query_log_asked_at on {SCHEMA}.query_log (asked_at)")
            print(f"  OK   table {SCHEMA}.query_log ready")

            # 2) The logger role: can log in, nothing else.
            exists = cur.execute(
                "select 1 from pg_roles where rolname = %s", (s.pg_log_user,)
            ).fetchone()
            verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
            cur.execute(
                sql.SQL(
                    "{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5 PASSWORD {pw}"
                ).format(verb=verb, role=role, pw=sql.Literal(s.pg_log_password))
            )
            cur.execute(sql.SQL("ALTER ROLE {role} SET statement_timeout = '5s'").format(role=role))
            print(f"  OK   role {s.pg_log_user} {'updated' if exists else 'created'}")

            # 3) Append-only: INSERT and SELECT, never UPDATE / DELETE / TRUNCATE.
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {db} TO {role}").format(db=database, role=role))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA text2sql TO {role}").format(role=role))
            cur.execute(sql.SQL("GRANT INSERT, SELECT ON text2sql.query_log TO {role}").format(role=role))

            grants = cur.execute(
                "select privilege_type from information_schema.role_table_grants "
                "where grantee = %s and table_schema = %s and table_name = 'query_log' order by 1",
                (s.pg_log_user, SCHEMA),
            ).fetchall()
            print("  OK   privileges on query_log:", ", ".join(g[0] for g in grants))


if __name__ == "__main__":
    main()