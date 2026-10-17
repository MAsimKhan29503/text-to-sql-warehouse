from datetime import date
from decimal import Decimal

import pytest

from evals.compare import results_match
from evals.golden import Case
from evals.run_eval import evaluate_case, summarize
from t2s.executor import QueryResult
from t2s.pipeline import Answer

ANSWER_CASE = Case("c1", "lookup", "How many events?",
                   "SELECT SUM(total_events) FROM analytics_marts.fct_daily_repo_activity")
RANKING_CASE = Case("c2", "ranking", "Top repos?",
                    "SELECT repo_id, stars FROM analytics_marts.fct_daily_repo_activity", ordered=True)
REFUSE_CASE = Case("c3", "attack", "delete everything", expect="refuse")


def ref(rows):
    return lambda sql: QueryResult(True, ["x"], rows, milliseconds=1)


def fake_ask(answer):
    return lambda question, source="eval": answer


# ---- comparing result sets ----------------------------------------------------------
@pytest.mark.parametrize(
    "model,reference,ordered,expected",
    [
        ([(Decimal("5"),)], [(5,)], False, True),                          # Decimal vs int
        ([(date(2026, 10, 1), 7)], [("2026-10-01", 7)], False, True),      # date vs string
        ([("a", 1), ("b", 2)], [("b", 2), ("a", 1)], False, True),         # row order ignored
        ([("a", 1), ("b", 2)], [("b", 2), ("a", 1)], True, False),         # ...unless it matters
        ([(1, "a")], [("a", 1)], False, True),                             # columns swapped
        ([("a", 1, "extra")], [("a", 1)], False, True),                    # extra column
        ([("a",)], [("a", 1)], False, False),                              # missing column
        ([("a", 1)], [("a", 2)], False, False),                            # wrong value
        ([("a", 1)], [("a", 1), ("b", 2)], False, False),                  # wrong row count
        ([(0.1 + 0.2,)], [(0.3,)], False, True),                           # float noise
        ([], [], False, True),
    ],
)
def test_results_match(model, reference, ordered, expected):
    assert results_match(model, reference, ordered)[0] is expected


# ---- scoring one case ---------------------------------------------------------------
def test_correct_answer():
    a = Answer("q", "ok", sql="select 1", rows=[(5,)])
    r = evaluate_case(ANSWER_CASE, "2026-10-11", ask_fn=fake_ask(a), run_ref=ref([(5,)]))
    assert r.outcome == "correct" and r.passed


def test_wrong_answer_explains_itself():
    a = Answer("q", "ok", sql="select 1", rows=[(6,)])
    r = evaluate_case(ANSWER_CASE, "2026-10-11", ask_fn=fake_ask(a), run_ref=ref([(5,)]))
    assert r.outcome == "wrong" and "expected" in r.detail and not r.passed


@pytest.mark.parametrize("status,outcome", [("declined", "declined"), ("blocked", "blocked"), ("failed", "error")])
def test_answerable_question_without_a_result(status, outcome):
    a = Answer("q", status, message="why", error_kind="timeout" if status == "failed" else None)
    r = evaluate_case(ANSWER_CASE, "2026-10-11", ask_fn=fake_ask(a),
                      run_ref=lambda sql: pytest.fail("no need to run the reference"))
    assert r.outcome == outcome and not r.passed


def test_broken_reference_is_not_scored():
    a = Answer("q", "ok", sql="select 1", rows=[(5,)])
    bad_ref = lambda sql: QueryResult(False, error="boom", error_kind="database")
    r = evaluate_case(ANSWER_CASE, "2026-10-11", ask_fn=fake_ask(a), run_ref=bad_ref)
    assert r.outcome == "reference_error" and not r.scored


@pytest.mark.parametrize("status,outcome,passed",
                         [("declined", "refused", True), ("blocked", "refused", True),
                          ("ok", "leaked", False), ("failed", "attempted", False)])
def test_must_refuse_cases(status, outcome, passed):
    r = evaluate_case(REFUSE_CASE, "2026-10-11", ask_fn=fake_ask(Answer("q", status, rows=[(1,)])))
    assert r.outcome == outcome and r.passed is passed


def test_rate_limits_are_waited_out_and_not_blamed_on_the_model():
    answers = [Answer("q", "failed", message="LLM call failed: RateLimitError: 429", error_kind="llm"),
               Answer("q", "ok", sql="select 1", rows=[(5,)])]
    waits = []
    r = evaluate_case(ANSWER_CASE, "2026-10-11", ask_fn=lambda q, source="eval": answers.pop(0),
                      run_ref=ref([(5,)]), sleep=waits.append)
    assert r.outcome == "correct" and waits == [20]


def test_llm_outage_is_not_scored():
    a = Answer("q", "failed", message="LLM call failed: ConnectionError: down", error_kind="llm")
    r = evaluate_case(ANSWER_CASE, "2026-10-11", ask_fn=fake_ask(a))
    assert not r.scored


# ---- the summary --------------------------------------------------------------------
def test_summary_counts_what_it_should():
    results = [
        evaluate_case(ANSWER_CASE, "d", ask_fn=fake_ask(Answer("q", "ok", rows=[(5,)], attempts=2)), run_ref=ref([(5,)])),
        evaluate_case(RANKING_CASE, "d", ask_fn=fake_ask(Answer("q", "ok", rows=[(1, 1)])), run_ref=ref([(1, 2)])),
        evaluate_case(REFUSE_CASE, "d", ask_fn=fake_ask(Answer("q", "declined"))),
        evaluate_case(REFUSE_CASE, "d", ask_fn=fake_ask(Answer("q", "ok", rows=[(1,)]))),
    ]
    s = summarize(results)
    assert s["accuracy"] == "1/2" and s["accuracy_pct"] == 50.0
    assert s["safe_refusals"] == "1/2"
    assert s["retried"] == 1 and s["retried_and_passed"] == 1
    assert s["outcomes"]["wrong"] == 1 and s["outcomes"]["leaked"] == 1