import re
import sys
import time
from dataclasses import dataclass
from functools import lru_cache


from groq import Groq

from t2s.config import get_settings
from t2s.schema_context import build_schema_context
from t2s.guardrails import validate_sql

PROMPT_TEMPLATE = """You are a PostgreSQL analyst for a data warehouse of GitHub public events.
Turn the user's question into ONE read-only SQL query.

{schema_context}

RULES
1. Output exactly one SQL statement inside a ```sql code fence, and nothing else.
2. Only SELECT queries (WITH ... SELECT is fine). Never modify data or schema.
3. Use only the tables and columns listed above, always schema-qualified (analytics_marts.table_name).
4. Prefer analytics_marts.fct_daily_repo_activity for counts, trends and rankings. Use analytics_marts.fct_events only when event-level detail is required.
5. Always include a LIMIT of at most 100 rows, unless the query returns a single row.
6. For human-only activity filter dim_actor.is_bot = false, or use unique_human_actors.
7. When the answer needs repository names or account names, join the dimension tables. Qualify every column with its table alias.
8. If the question cannot be answered with these tables, output exactly one line: -- CANNOT_ANSWER: <short reason>
9. Relative dates are relative to the last day of data. "Last N days" or "last week" (7 days) means the N days ending on and including that last day; "yesterday" means the day before it. Use the last day from DATA COVERS as a literal date.
"""

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class Generation:
    question: str
    sql: str | None       # the generated query, if any
    refusal: str | None   # the model's reason, if it declined
    raw: str              # exact model output, kept for logging and debugging
    seconds: float
    total_tokens: int


@lru_cache(maxsize=1)
def _schema_context() -> str:
    return build_schema_context()  # hits the database once per run, then cached


def extract_sql(text: str) -> str:
    match = _FENCE.search(text)
    return (match.group(1) if match else text).strip()


def generate_sql(question: str) -> Generation:
    s = get_settings()
    client = Groq(api_key=s.groq_api_key)

    start = time.perf_counter()
    resp = client.chat.completions.create(
        model=s.groq_model,
        messages=[
            {"role": "system", "content": PROMPT_TEMPLATE.format(schema_context=_schema_context())},
            {"role": "user", "content": question},
        ],
        temperature=0,
        max_completion_tokens=2048,  # reasoning tokens count too, so keep this generous
        reasoning_effort="medium",
    )
    seconds = time.perf_counter() - start

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        raise RuntimeError("Model returned no text. Try raising max_completion_tokens.")

    sql = extract_sql(raw)
    if sql.upper().startswith("-- CANNOT_ANSWER"):
        return Generation(question, None, sql.removeprefix("-- CANNOT_ANSWER:").strip(),
                          raw, seconds, resp.usage.total_tokens)
    return Generation(question, sql, None, raw, seconds, resp.usage.total_tokens)


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        sys.exit('Usage: python -m t2s.generate "your question"')
    g = generate_sql(question)
    print(f"Question: {g.question}\n")
    if g.sql:
        check = validate_sql(g.sql)
        if check.ok:
            print("Validated SQL (this is exactly what would run):\n")
            print(check.sql)
        else:
            print(f"BLOCKED by guardrails: {check.error}\n")
            print(f"Model wrote:\n{g.sql}")
    else:
        print(f"Model declined: {g.refusal}")
    print(f"\n[{g.seconds:.1f}s, {g.total_tokens} tokens]")