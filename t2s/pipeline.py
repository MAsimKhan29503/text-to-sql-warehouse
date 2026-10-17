from dataclasses import dataclass, field

from t2s.executor import run_query
from t2s.generate import generate_sql
from t2s.guardrails import validate_sql
from t2s.query_log import log_answer

MAX_ATTEMPTS = 2  # the first try plus ONE correction


@dataclass
class Answer:
    question: str
    status: str                       # ok | declined | blocked | failed
    sql: str | None = None            # validated SQL that was (or would have been) run
    raw_sql: str | None = None        # exactly what the model wrote (last attempt)
    message: str | None = None        # explanation for any non-ok status
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    llm_seconds: float = 0.0          # totals across all attempts
    db_ms: int = 0
    tokens: int = 0
    attempts: int = 1
    error_kind: str | None = None     # guardrail | timeout | resources | database | unavailable | llm


def _feedback(problem: str) -> str:
    return (
        f"That query did not work: {problem}\n"
        "Write ONE corrected query. Follow every rule above and answer in the same format."
    )


def _answer(question: str) -> Answer:
    history: list[dict] = []
    llm_seconds, tokens, db_ms = 0.0, 0, 0
    first_problem: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            gen = generate_sql(question, history)
        except Exception as e:  # network error, rate limit, bad key: report it, don't crash
            return Answer(question, "failed", message=f"LLM call failed: {type(e).__name__}: {e}",
                          llm_seconds=llm_seconds, tokens=tokens, db_ms=db_ms, attempts=attempt,
                          error_kind="llm")
        llm_seconds += gen.seconds
        tokens += gen.total_tokens
        totals = dict(question=question, raw_sql=gen.sql, llm_seconds=llm_seconds,
                      tokens=tokens, attempts=attempt)

        if gen.sql is None:  # the model said it cannot answer: never retried
            return Answer(status="declined", message=gen.refusal, db_ms=db_ms, **totals)

        check = validate_sql(gen.sql)
        if check.ok:
            result = run_query(check.sql)
            db_ms += result.milliseconds
            if result.ok:
                note = f"Succeeded on attempt {attempt} after: {first_problem}" if first_problem else None
                return Answer(status="ok", sql=check.sql, message=note, columns=result.columns,
                              rows=result.rows, db_ms=db_ms, **totals)
            if result.error_kind == "unavailable":  # not the model's fault: retrying cannot help
                return Answer(status="failed", sql=check.sql, message=result.error, db_ms=db_ms,
                              error_kind="unavailable", **totals)
            status, problem, sql_used, kind = "failed", result.error, check.sql, result.error_kind
        else:
            if not check.retryable:  # a policy violation, not a slip: never "fix" what the user asked for
                return Answer(status="blocked", message=check.error, db_ms=db_ms,
                              error_kind="guardrail", **totals)
            status, problem, sql_used, kind = "blocked", check.error, None, "guardrail"

        first_problem = first_problem or problem
        if attempt == MAX_ATTEMPTS:
            return Answer(status=status, sql=sql_used, message=problem, db_ms=db_ms,
                          error_kind=kind, **totals)

        # Show the model its own answer and what went wrong, then try again.
        history += [
            {"role": "assistant", "content": gen.raw},
            {"role": "user", "content": _feedback(problem)},
        ]

    raise AssertionError("unreachable")  # the loop always returns


def ask(question: str, source: str = "cli") -> Answer:
    """The single entry point: generate, validate, execute, log."""
    answer = _answer(question)
    log_answer(answer, source)
    return answer