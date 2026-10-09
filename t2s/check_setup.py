import sys

import psycopg
from groq import Groq

from t2s.config import get_settings


def check_postgres(s) -> bool:
    print("[1/2] PostgreSQL ...")
    try:
        with psycopg.connect(
            host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
            user=s.pg_user, password=s.pg_password, connect_timeout=5,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("select version()")
                print("  OK  ", cur.fetchone()[0].split(",")[0])

                cur.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'analytics_marts' order by table_name"
                )
                print("  OK   analytics_marts:", ", ".join(r[0] for r in cur.fetchall()))

                cur.execute("select count(*) from analytics_marts.fct_events")
                print(f"  OK   fct_events rows: {cur.fetchone()[0]:,}")
        return True
    except Exception as e:
        print("  FAIL", type(e).__name__, "-", e)
        return False


def check_groq(s) -> bool:
    print("[2/2] Groq ...")
    try:
        client = Groq(api_key=s.groq_api_key)
        resp = client.chat.completions.create(
            model=s.groq_model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            max_completion_tokens=512,
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        print(f"  OK   {s.groq_model} replied: {text!r} ({resp.usage.total_tokens} tokens)")
        return True
    except Exception as e:
        print("  FAIL", type(e).__name__, "-", e)
        return False


if __name__ == "__main__":
    settings = get_settings()
    results = [check_postgres(settings), check_groq(settings)]
    sys.exit(0 if all(results) else 1)