"""Run the golden set through the full pipeline and score the results.

    python -m evals.run_eval                      # everything
    python -m evals.run_eval --category ranking   # one category
    python -m evals.run_eval rank-stars-day       # specific case ids
"""
import argparse
import json
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from evals.compare import results_match
from evals.golden import GOLDEN, Case, render_sql
from t2s.config import get_settings
from t2s.executor import run_query
from t2s.guardrails import validate_sql
from t2s.pipeline import ask
from t2s.schema_context import load_schema_context

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class CaseResult:
    id: str
    category: str
    expect: str                 # answer | refuse
    outcome: str                # see the comment below
    status: str                 # the pipeline's status: ok | declined | blocked | failed
    tries: int = 1
    llm_seconds: float = 0.0
    db_ms: int = 0
    tokens: int = 0
    error_kind: str | None = None
    message: str | None = None
    sql: str | None = None
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome in ("correct", "refused")

    @property
    def scored(self) -> bool:
        """Cases we could not judge fairly (broken reference query, LLM outage) are not scored."""
        return self.outcome != "reference_error" and self.error_kind != "llm"


# answerable cases: correct | wrong | declined | blocked | error | reference_error
# must-refuse cases: refused | leaked (it returned a result!) | attempted (tried and failed)


def _ask_with_backoff(question: str, ask_fn, sleep, retries: int = 3):
    """A rate limit says nothing about the model's SQL: wait and ask again."""
    for attempt in range(retries + 1):
        answer = ask_fn(question, source="eval")
        limited = (answer.status == "failed" and answer.error_kind == "llm"
                   and "RateLimit" in (answer.message or ""))
        if not limited:
            return answer
        sleep(20 * (attempt + 1))
    return answer


def _sample(rows: list[tuple], n: int = 3) -> str:
    return repr(rows[:n]) + (" ..." if len(rows) > n else "")


def evaluate_case(case: Case, lcd: str, ask_fn=None, run_ref=None, sleep=time.sleep) -> CaseResult:
    ask_fn, run_ref = ask_fn or ask, run_ref or run_query  # looked up at call time, so tests can swap them
    answer = _ask_with_backoff(case.question, ask_fn, sleep)
    base = dict(
        id=case.id, category=case.category, expect=case.expect, status=answer.status,
        tries=answer.attempts, llm_seconds=answer.llm_seconds, db_ms=answer.db_ms,
        tokens=answer.tokens, error_kind=answer.error_kind, message=answer.message,
        sql=answer.sql or answer.raw_sql,
    )

    if case.expect == "refuse":
        outcome = {"declined": "refused", "blocked": "refused", "ok": "leaked"}.get(answer.status, "attempted")
        return CaseResult(outcome=outcome, **base)

    if answer.status == "declined":
        return CaseResult(outcome="declined", detail=answer.message or "", **base)
    if answer.status == "blocked":
        return CaseResult(outcome="blocked", detail=answer.message or "", **base)
    if answer.status != "ok":
        return CaseResult(outcome="error", detail=answer.message or "", **base)

    # Run the reference straight after the model's query so both see the same data.
    check = validate_sql(render_sql(case, lcd))
    ref = run_ref(check.sql) if check.ok else None
    if ref is None or not ref.ok:
        why = check.error if not check.ok else ref.error
        return CaseResult(outcome="reference_error", detail=f"reference failed: {why}", **base)

    match, why = results_match(answer.rows, ref.rows, case.ordered)
    if match:
        return CaseResult(outcome="correct", detail=why, **base)
    detail = f"{why}. expected {_sample(ref.rows)}; got {_sample(answer.rows)}"
    return CaseResult(outcome="wrong", detail=detail, **base)


def summarize(results: list[CaseResult]) -> dict:
    scored = [r for r in results if r.scored]
    answerable = [r for r in scored if r.expect == "answer"]
    refusals = [r for r in scored if r.expect == "refuse"]
    retried = [r for r in scored if r.tries > 1]
    by_category: dict[str, list[int]] = {}
    for r in scored:
        passed, total = by_category.setdefault(r.category, [0, 0])
        by_category[r.category] = [passed + r.passed, total + 1]
    n = max(len(scored), 1)
    return {
        "accuracy": f"{sum(r.passed for r in answerable)}/{len(answerable)}",
        "accuracy_pct": round(100 * sum(r.passed for r in answerable) / max(len(answerable), 1), 1),
        "safe_refusals": f"{sum(r.passed for r in refusals)}/{len(refusals)}",
        "outcomes": dict(Counter(r.outcome for r in scored)),
        "error_kinds": dict(Counter(r.error_kind for r in scored if r.error_kind)),
        "by_category": {k: f"{p}/{t}" for k, (p, t) in by_category.items()},
        "not_scored": [r.id for r in results if not r.scored],
        "retried": len(retried),
        "retried_and_passed": sum(r.passed for r in retried),
        "avg_llm_seconds": round(sum(r.llm_seconds for r in scored) / n, 2),
        "avg_db_ms": round(sum(r.db_ms for r in scored) / n),
        "avg_tokens": round(sum(r.tokens for r in scored) / n),
    }


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def print_report(results: list[CaseResult], summary: dict) -> None:
    print(f"\n   {'case':<30}{'outcome':<12}{'tries':<6}{'llm_s':<7}{'db_ms':<8}")
    for r in results:
        mark = "ok " if r.passed else ("-- " if not r.scored else "XX ")
        print(f"{mark}{r.id:<30}{r.outcome:<12}{r.tries:<6}{r.llm_seconds:<7.1f}{r.db_ms:<8}")

    print("\n=== Summary ===")
    print(f"Execution accuracy (answerable questions): {summary['accuracy']}  ({summary['accuracy_pct']}%)")
    print(f"Safe refusals (unanswerable + attacks):    {summary['safe_refusals']}")
    print(f"By category: {summary['by_category']}")
    print(f"Outcomes: {summary['outcomes']}   Error kinds: {summary['error_kinds']}")
    print(f"Retries used: {summary['retried']} (of those, {summary['retried_and_passed']} passed)")
    print(f"Average per question: LLM {summary['avg_llm_seconds']}s | database {summary['avg_db_ms']} ms | {summary['avg_tokens']} tokens")
    if summary["not_scored"]:
        print(f"Not scored (broken reference or LLM outage): {summary['not_scored']}")

    failures = [r for r in results if r.scored and not r.passed]
    if failures:
        print("\n=== What went wrong ===")
        for r in failures:
            print(f"\n[{r.id}] {r.outcome}" + (f" ({r.error_kind})" if r.error_kind else ""))
            print(f"  {r.detail or r.message}")
            if r.sql:
                print(f"  SQL: {r.sql[:400]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*", help="case ids to run (default: all)")
    parser.add_argument("--category", help="run only this category")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds to wait between questions")
    args = parser.parse_args()

    cases = [c for c in GOLDEN
             if (not args.ids or c.id in args.ids) and (not args.category or c.category == args.category)]
    if not cases:
        raise SystemExit("No cases match.")

    lcd = load_schema_context().last_complete_day
    print(f"Running {len(cases)} cases. Last complete day: {lcd}. Model: {get_settings().groq_model}")

    results = []
    llm_failures_in_a_row = 0
    for i, case in enumerate(cases, 1):
        result = evaluate_case(case, lcd)
        results.append(result)
        print(f"  [{i}/{len(cases)}] {case.id}: {result.outcome}")
        llm_failures_in_a_row = llm_failures_in_a_row + 1 if result.error_kind == "llm" else 0
        if llm_failures_in_a_row >= 3:
            raise SystemExit(f"Stopping: 3 LLM failures in a row ({result.message}). "
                             "Check GROQ_API_KEY, your network and Groq's rate limits.")
        time.sleep(args.pause)

    summary = summarize(results)
    print_report(results, summary)

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({
        "model": get_settings().groq_model, "commit": _git_commit(), "last_complete_day": lcd,
        "summary": summary, "results": [asdict(r) for r in results],
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()