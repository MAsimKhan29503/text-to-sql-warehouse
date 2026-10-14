import psycopg

from t2s.ask import format_table
from t2s.config import get_settings


def main() -> None:
    s = get_settings()
    with psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_log_user, password=s.pg_log_password, connect_timeout=5,
    ) as conn:
        summary = conn.execute(
            "select status, count(*), round(avg(llm_seconds), 1), round(avg(db_ms)) "
            "from text2sql.query_log group by status order by 2 desc"
        ).fetchall()
        recent = conn.execute(
            "select id, to_char(asked_at, 'MM-DD HH24:MI'), status, left(question, 50), llm_seconds, db_ms "
            "from text2sql.query_log order by id desc limit 10"
        ).fetchall()

    print("By status")
    print(format_table(["status", "count", "avg_llm_s", "avg_db_ms"], summary))
    print("\nLast 10 questions")
    print(format_table(["id", "when", "status", "question", "llm_s", "db_ms"], recent))


if __name__ == "__main__":
    main()