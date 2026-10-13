from types import SimpleNamespace

import pytest

import t2s.generate as gen_mod
import t2s.pipeline as pipeline
from t2s.executor import QueryResult
from t2s.generate import Generation


def fake_generation(sql=None, refusal=None):
    return Generation("q", sql, refusal, sql or refusal or "", 0.1, 10)


def test_declined_never_reaches_the_database(monkeypatch):
    monkeypatch.setattr(pipeline, "generate_sql", lambda q: fake_generation(refusal="no such data"))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: pytest.fail("must not run"))
    a = pipeline.ask("q")
    assert a.status == "declined" and a.message == "no such data"


def test_blocked_sql_never_reaches_the_database(monkeypatch):
    monkeypatch.setattr(pipeline, "generate_sql", lambda q: fake_generation(sql="drop table analytics_marts.dim_repo"))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: pytest.fail("must not run"))
    a = pipeline.ask("q")
    assert a.status == "blocked" and a.sql is None and a.raw_sql.startswith("drop")


def test_ok_runs_the_normalized_sql_not_the_raw_text(monkeypatch):
    seen = {}

    def fake_run(sql):
        seen["sql"] = sql
        return QueryResult(True, ["repo_id"], [(1,)], milliseconds=5)

    monkeypatch.setattr(pipeline, "generate_sql", lambda q: fake_generation(sql="select repo_id from analytics_marts.dim_repo"))
    monkeypatch.setattr(pipeline, "run_query", fake_run)
    a = pipeline.ask("q")
    assert a.status == "ok" and a.rows == [(1,)]
    assert seen["sql"].endswith("LIMIT 100")  # the forced LIMIT was applied before execution


def test_database_error_is_reported(monkeypatch):
    monkeypatch.setattr(pipeline, "generate_sql", lambda q: fake_generation(sql="select nope from analytics_marts.dim_repo"))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: QueryResult(False, error='column "nope" does not exist', error_kind="database"))
    a = pipeline.ask("q")
    assert a.status == "failed" and "nope" in a.message and a.sql


def test_llm_failure_is_reported_not_raised(monkeypatch):
    def boom(q):
        raise ConnectionError("network down")

    monkeypatch.setattr(pipeline, "generate_sql", boom)
    a = pipeline.ask("q")
    assert a.status == "failed" and "network down" in a.message


@pytest.mark.parametrize(
    "model_output,expects_sql",
    [
        ("```sql\nSELECT 1\n```", True),
        ("SELECT 1", True),
        ("WITH a AS (SELECT 1) SELECT * FROM a", True),
        ("```sql\n-- CANNOT_ANSWER: no such column\n```", False),
        ("-- CANNOT_ANSWER: no such column", False),
        ("I'm sorry, but I can't help with that.", False),
    ],
)
def test_model_output_is_classified(monkeypatch, model_output, expects_sql):
    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=model_output))],
        usage=SimpleNamespace(total_tokens=1),
    )
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: fake_resp)))
    monkeypatch.setattr(gen_mod, "Groq", lambda api_key: fake_client)
    monkeypatch.setattr(gen_mod, "get_settings", lambda: SimpleNamespace(groq_api_key="x", groq_model="m"))
    monkeypatch.setattr(gen_mod, "_schema_context", lambda: "SCHEMA")
    g = gen_mod.generate_sql("q")
    assert (g.sql is not None) == expects_sql
    assert (g.refusal is not None) == (not expects_sql)