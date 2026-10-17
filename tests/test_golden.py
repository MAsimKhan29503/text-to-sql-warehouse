import pytest

from evals.golden import GOLDEN, render_sql
from t2s.guardrails import validate_sql

ANSWERABLE = [c for c in GOLDEN if c.expect == "answer"]
CATEGORIES = {"lookup", "time", "ranking", "actors", "filter", "hard", "unanswerable", "attack"}


def test_ids_and_questions_are_unique():
    assert len({c.id for c in GOLDEN}) == len(GOLDEN)
    assert len({c.question for c in GOLDEN}) == len(GOLDEN)


def test_every_case_is_well_formed():
    for c in GOLDEN:
        assert c.category in CATEGORIES, c.id
        assert c.expect in {"answer", "refuse"}, c.id
        assert (c.reference_sql is not None) == (c.expect == "answer"), c.id


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c.id)
def test_reference_sql_passes_our_own_guardrails(case):
    result = validate_sql(render_sql(case, "2026-10-11"))
    assert result.ok, result.error


def test_the_set_covers_every_category():
    assert {c.category for c in GOLDEN} == CATEGORIES