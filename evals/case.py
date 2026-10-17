from dataclasses import dataclass

A = "analytics_marts"


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    question: str
    reference_sql: str | None = None   # None for questions that must be refused
    expect: str = "answer"             # "answer" or "refuse"
    ordered: bool = False              # does row order matter when comparing results?
    decimals: int = 4                  # numbers are compared after rounding to this many decimals


def render_sql(case: Case, last_complete_day: str) -> str:
    return case.reference_sql.format(lcd=last_complete_day)