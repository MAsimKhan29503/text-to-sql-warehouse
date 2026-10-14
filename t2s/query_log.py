import logging
from typing import TYPE_CHECKING

import psycopg

from t2s.config import get_settings

if TYPE_CHECKING:
    from t2s.pipeline import Answer

log = logging.getLogger(__name__)

INSERT = """
insert into text2sql.query_log
    (source, question, status, raw_sql, executed_sql, message,
     row_count, llm_seconds, db_ms, tokens, model)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def log_answer(answer: "Answer", source: str = "cli") -> None:
    """Record one question and its outcome. Logging must NEVER break the app."""
    try:
        s = get_settings()
        params = (
            source, answer.question, answer.status, answer.raw_sql, answer.sql, answer.message,
            len(answer.rows) if answer.status == "ok" else None,
            round(answer.llm_seconds, 2), answer.db_ms, answer.tokens, s.groq_model,
        )
        with psycopg.connect(
            host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
            user=s.pg_log_user, password=s.pg_log_password, connect_timeout=3,
        ) as conn:
            conn.execute(INSERT, params)
    except Exception as e:
        log.warning("Could not write to the query log: %s", e)