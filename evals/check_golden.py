"""Run every reference query against the warehouse and report problems with the golden set itself."""
import sys
import time

from evals.golden import GOLDEN, render_sql
from t2s.executor import run_query
from t2s.guardrails import validate_sql
from t2s.schema_context import load_schema_context


def main() -> int:
    lcd = load_schema_context().last_complete_day
    print(f"Last complete day: {lcd}\n")
    problems = 0
    wanted = set(sys.argv[1:])  # optional: run only the case ids given on the command line
    answerable = [c for c in GOLDEN if c.expect == "answer" and (not wanted or c.id in wanted)]

    for case in answerable:
        check = validate_sql(render_sql(case, lcd))
        if not check.ok:
            print(f"  FAIL {case.id}: reference SQL rejected by guardrails: {check.error}")
            problems += 1
            continue
        start = time.perf_counter()
        result = run_query(check.sql)
        seconds = time.perf_counter() - start
        if not result.ok:
            print(f"  FAIL {case.id}: {result.error}")
            problems += 1
        elif not result.rows or all(v is None for row in result.rows for v in row):
            print(f"  WARN {case.id}: reference returned no data ({seconds:.1f}s): check the dates")
        else:
            print(f"  OK   {case.id}: {len(result.rows)} rows ({seconds:.1f}s)")

    refused = [c for c in GOLDEN if c.expect == "refuse"]
    print(f"\n{len(answerable)} answerable cases, {len(refused)} must-refuse cases, {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())