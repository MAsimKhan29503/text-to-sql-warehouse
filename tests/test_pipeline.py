from types import SimpleNamespace

import pytest

import t2s.generate as gen_mod
import t2s.pipeline as pipeline
from t2s.executor import QueryResult
from t2s.generate import Generation

GOOD = "select repo_id from analytics_marts.dim_repo"


def fake_generation(sql=None, refusal=None):
    return Generation("q", sql, refusal, sql or refusal or "", 0.1, 10)


def script(monkeypatch, *generations):
    """Make generate_sql return the given generations in order; record the history it was given."""
    calls = []
    queue = list(generations)

    def fake(question, history=None):
        calls.append(list(history or []))
        return queue.pop(0)

    monkeypatch.setattr(pipeline, "generate_sql", fake)
    return calls


def test_declined_never_reaches_the_database_and_is_not_retried(monkeypatch):
    calls = script(monkeypatch, fake_generation(refusal="no such data"))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: pytest.fail("must not run"))
    a = pipeline.ask("q")
    assert a.status == "declined" and a.message == "no such data"
    assert a.attempts == 1 and len(calls) == 1


def test_blocked_twice_is_blocked_and_never_reaches_the_database(monkeypatch):
    bad = fake_generation(sql="drop table analytics_marts.dim_repo")
    calls = script(monkeypatch, bad, bad)
    monkeypatch.setattr(pipeline, "run_query", lambda sql: pytest.fail("must not run"))
    a = pipeline.ask("q")
    assert a.status == "blocked" and a.sql is None and a.raw_sql.startswith("drop")
    assert a.attempts == pipeline.MAX_ATTEMPTS and len(calls) == pipeline.MAX_ATTEMPTS


def test_ok_first_try_has_one_attempt_and_runs_normalized_sql(monkeypatch):
    seen = {}

    def fake_run(sql):
        seen["sql"] = sql
        return QueryResult(True, ["repo_id"], [(1,)], milliseconds=5)

    script(monkeypatch, fake_generation(sql=GOOD))
    monkeypatch.setattr(pipeline, "run_query", fake_run)
    a = pipeline.ask("q")
    assert a.status == "ok" and a.rows == [(1,)] and a.attempts == 1 and a.message is None
    assert seen["sql"].endswith("LIMIT 100")  # the forced LIMIT was applied before execution


def test_blocked_then_fixed_succeeds_on_attempt_two(monkeypatch):
    calls = script(monkeypatch,
                   fake_generation(sql="select * from raw.gh_events"),
                   fake_generation(sql=GOOD))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: QueryResult(True, ["repo_id"], [(1,)], milliseconds=5))
    a = pipeline.ask("q")
    assert a.status == "ok" and a.attempts == 2
    assert "raw.gh_events" in a.message                      # the logged reason for the retry
    assert calls[0] == []                                    # first attempt: no history
    assert "Table not allowed" in calls[1][-1]["content"]    # second attempt saw the error
    assert calls[1][0]["role"] == "assistant"                # ...and its own earlier answer


def test_database_error_then_fixed_succeeds_and_totals_add_up(monkeypatch):
    results = [QueryResult(False, error='column "nope" does not exist', error_kind="database", milliseconds=40),
               QueryResult(True, ["repo_id"], [(1,)], milliseconds=60)]
    calls = script(monkeypatch,
                   fake_generation(sql="select nope from analytics_marts.dim_repo"),
                   fake_generation(sql=GOOD))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: results.pop(0))
    a = pipeline.ask("q")
    assert a.status == "ok" and a.attempts == 2
    assert a.db_ms == 100 and a.tokens == 20 and a.llm_seconds == pytest.approx(0.2)
    assert 'column "nope" does not exist' in calls[1][-1]["content"]


def test_database_error_twice_is_reported_as_failed(monkeypatch):
    script(monkeypatch,
           fake_generation(sql="select nope from analytics_marts.dim_repo"),
           fake_generation(sql="select nope2 from analytics_marts.dim_repo"))
    monkeypatch.setattr(pipeline, "run_query",
                        lambda sql: QueryResult(False, error='column "nope2" does not exist', error_kind="database"))
    a = pipeline.ask("q")
    assert a.status == "failed" and "nope2" in a.message and a.sql and a.attempts == 2


def test_database_outage_is_not_retried(monkeypatch):
    calls = script(monkeypatch, fake_generation(sql=GOOD), fake_generation(sql=GOOD))
    monkeypatch.setattr(pipeline, "run_query",
                        lambda sql: QueryResult(False, error="The database is unavailable: refused", error_kind="unavailable"))
    a = pipeline.ask("q")
    assert a.status == "failed" and a.attempts == 1 and len(calls) == 1

def test_resource_errors_are_retried_with_the_message(monkeypatch):
    results = [QueryResult(False, error="needed more memory", error_kind="resources"),
               QueryResult(True, ["repo_id"], [(1,)], milliseconds=5)]
    calls = script(monkeypatch, fake_generation(sql=GOOD), fake_generation(sql=GOOD))
    monkeypatch.setattr(pipeline, "run_query", lambda sql: results.pop(0))
    a = pipeline.ask("q")
    assert a.status == "ok" and a.attempts == 2
    assert "needed more memory" in calls[1][-1]["content"]


def test_llm_failure_is_reported_not_raised(monkeypatch):
    def boom(question, history=None):
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


def test_history_is_sent_to_the_model_after_the_question(monkeypatch):
    sent = {}

    def create(**kw):
        sent["messages"] = kw["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))],
                               usage=SimpleNamespace(total_tokens=1))

    monkeypatch.setattr(gen_mod, "Groq", lambda api_key: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(gen_mod, "get_settings", lambda: SimpleNamespace(groq_api_key="x", groq_model="m"))
    monkeypatch.setattr(gen_mod, "_schema_context", lambda: "SCHEMA")
    history = [{"role": "assistant", "content": "SELECT oops"}, {"role": "user", "content": "fix it"}]
    gen_mod.generate_sql("the question", history)
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert sent["messages"][1]["content"] == "the question"





def test_failures_carry_a_machine_readable_kind(monkeypatch):
    bad = fake_generation(sql="drop table analytics_marts.dim_repo")
    script(monkeypatch, bad, bad)
    assert pipeline.ask("q").error_kind == "guardrail"

    script(monkeypatch, fake_generation(sql=GOOD), fake_generation(sql=GOOD))
    monkeypatch.setattr(pipeline, "run_query",
                        lambda sql: QueryResult(False, error="too slow", error_kind="timeout"))
    assert pipeline.ask("q").error_kind == "timeout"

    def boom(question, history=None):
        raise ConnectionError("down")

    monkeypatch.setattr(pipeline, "generate_sql", boom)
    assert pipeline.ask("q").error_kind == "llm"