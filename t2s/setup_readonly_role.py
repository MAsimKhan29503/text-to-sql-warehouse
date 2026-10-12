import psycopg
from psycopg import sql

from t2s.config import get_settings

SCHEMA = "analytics_marts"


def main() -> None:
    s = get_settings()
    role = sql.Identifier(s.pg_ro_user)
    owner = sql.Identifier(s.pg_user)
    schema = sql.Identifier(SCHEMA)
    database = sql.Identifier(s.pg_db)

    # Connect as the OWNER account (the only time we do this).
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_user, password=s.pg_password, connect_timeout=5,
    ) as conn:
        with conn.cursor() as cur:
            exists = cur.execute(
                "select 1 from pg_roles where rolname = %s", (s.pg_ro_user,)
            ).fetchone()

            # 1) The role itself: can log in, can do nothing else.
            verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
            cur.execute(
                sql.SQL(
                    "{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION CONNECTION LIMIT 5 PASSWORD {pw}"
                ).format(verb=verb, role=role, pw=sql.Literal(s.pg_ro_password))
            )
            print(f"  OK   role {s.pg_ro_user} {'updated' if exists else 'created'}")

            # 2) Safety settings that apply to every session of this role.
            for setting in (
                "statement_timeout = '30s'",
                "idle_in_transaction_session_timeout = '30s'",
                "default_transaction_read_only = on",
            ):
                cur.execute(
                    sql.SQL("ALTER ROLE {role} SET ").format(role=role) + sql.SQL(setting)
                )
            print("  OK   timeouts and read-only default set")

            # 3) Permissions: connect, see one schema, SELECT its tables.
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {db} TO {role}").format(db=database, role=role))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {sc} TO {role}").format(sc=schema, role=role))
            cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {sc} TO {role}").format(sc=schema, role=role))

            # 4) Tables dbt creates LATER should also be readable.
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {sc} "
                    "GRANT SELECT ON TABLES TO {role}"
                ).format(owner=owner, sc=schema, role=role)
            )
            print(f"  OK   SELECT granted on {SCHEMA} (existing and future tables)")

            # 5) Show what the role can now do.
            grants = cur.execute(
                "select table_name, string_agg(privilege_type, ', ') "
                "from information_schema.role_table_grants "
                "where grantee = %s and table_schema = %s group by table_name order by 1",
                (s.pg_ro_user, SCHEMA),
            ).fetchall()
            for table, privs in grants:
                print(f"       {table}: {privs}")

            owners = {
                r[0] for r in cur.execute(
                    "select distinct tableowner from pg_tables where schemaname = %s", (SCHEMA,)
                ).fetchall()
            }
            if owners != {s.pg_user}:
                print(f"  WARN tables are owned by {owners}, not {s.pg_user}: "
                      "future dbt tables may not be readable. Tell me and we'll adjust.")


if __name__ == "__main__":
    main()