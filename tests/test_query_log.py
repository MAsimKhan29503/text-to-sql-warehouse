from types import SimpleNamespace

import t2s.pipeline as pipeline
import t2s.query_log as query_log
from t2s.generate import Generation


def test_every_answer_is_logged_with_its_source(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "log_answer", lambda answer, source: seen.append((answer.status, source)))
    monkeypatch.setattr(pipeline, "generate_sql", lambda q, history=None: Generation(q, None, "no such data", "", 0.1, 5))
    pipeline.ask("q", source="eval")
    assert seen == [("declined", "eval")]


def test_logging_failure_never_breaks_the_app(monkeypatch):
    settings = SimpleNamespace(
        pg_host="h", pg_port=1, pg_db="d", pg_log_user="u", pg_log_password="p", groq_model="m"
    )

    def refuse(**kwargs):
        raise OSError("database is down")

    monkeypatch.setattr(query_log, "get_settings", lambda: settings)
    monkeypatch.setattr(query_log.psycopg, "connect", refuse)
    query_log.log_answer(pipeline.Answer("q", "ok"))  # must not raise