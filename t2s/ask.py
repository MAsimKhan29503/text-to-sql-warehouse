import sys

from t2s.pipeline import ask


def format_table(columns: list[str], rows: list[tuple], max_width: int = 40) -> str:
    cells = [["NULL" if v is None else str(v)[:max_width] for v in row] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    line = lambda values: "  ".join(v.ljust(w) for v, w in zip(values, widths))
    out = [line(columns), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in cells)
    return "\n".join(out)


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        sys.exit('Usage: python -m t2s.ask "your question"')

    a = ask(question)
    print(f"Question: {a.question}\n")

    if a.status == "ok":
        print(f"SQL:\n{a.sql}\n")
        print(format_table(a.columns, a.rows))
        print(f"\n{len(a.rows)} rows | LLM {a.llm_seconds:.1f}s | database {a.db_ms} ms | {a.tokens} tokens")
        if a.attempts > 1:
            print(f"(took {a.attempts} attempts: {a.message})")
    else:
        print(f"[{a.status.upper()}] {a.message}")
        if a.attempts > 1:
            print(f"(after {a.attempts} attempts)")
        if a.sql:
            print(f"\nSQL that failed:\n{a.sql}")
        elif a.raw_sql:
            print(f"\nModel wrote:\n{a.raw_sql}")