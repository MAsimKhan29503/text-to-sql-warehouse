from dataclasses import dataclass, field

from t2s.executor import run_query
from t2s.generate import generate_sql
from t2s.guardrails import validate_sql
from t2s.query_log import log_answer

@dataclass
class Answer:
    question: str
    status: str                       # ok | declined | blocked | failed
    sql: str | None = None            # validated SQL that was (or would have been) run
    raw_sql: str | None = None        # exactly what the model wrote
    message: str | None = None        # explanation for any non-ok status
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    llm_seconds: float = 0.0
    db_ms: int = 0
    tokens: int = 0


def _answer(question: str) -> Answer:
    try:
        gen = generate_sql(question)
    except Exception as e:  # network error, rate limit, bad key: report it, don't crash
        return Answer(question, "failed", message=f"LLM call failed: {type(e).__name__}: {e}")

    base = dict(question=question, raw_sql=gen.sql, llm_seconds=gen.seconds, tokens=gen.total_tokens)

    if gen.sql is None:
        return Answer(status="declined", message=gen.refusal, **base)

    check = validate_sql(gen.sql)
    if not check.ok:
        return Answer(status="blocked", message=check.error, **base)

    result = run_query(check.sql)
    if not result.ok:
        return Answer(status="failed", sql=check.sql, message=result.error,
                      db_ms=result.milliseconds, **base)

    return Answer(status="ok", sql=check.sql, columns=result.columns, rows=result.rows,
                  db_ms=result.milliseconds, **base)

def ask(question: str, source: str = "cli") -> Answer:
       """The single entry point: generate, validate, execute, log."""
       answer = _answer(question)
       log_answer(answer, source)
       return answer