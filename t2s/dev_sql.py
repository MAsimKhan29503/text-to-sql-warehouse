import sys

import psycopg

from t2s.config import get_settings


def main() -> None:
    """Developer tool: run ONE statement as the read-only role and print the rows."""
    sql = " ".join(sys.argv[1:]).strip()
    if not sql:
        sys.exit('Usage: python -m t2s.dev_sql "select ..."')
    s = get_settings()
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_ro_user, password=s.pg_ro_password, connect_timeout=5,
    ) as conn:
        for row in conn.execute(sql).fetchall():
            print(*row, sep=" | ")


if __name__ == "__main__":
    main()