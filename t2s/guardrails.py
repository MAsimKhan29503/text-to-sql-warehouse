from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_SCHEMA = "analytics_marts"
ALLOWED_TABLES = {
    "dim_repo",
    "dim_actor",
    "fct_events",
    "fct_daily_repo_activity",
}
MAX_ROWS = 100

# Functions that read files, sleep, change settings, or reach other servers.
BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "set_config", "current_setting",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "lo_import", "lo_export", "dblink", "dblink_exec", "query_to_xml",
    "generate_series",
}

# Statement types that must never appear anywhere in the query tree.
BLOCKED_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create,
    exp.Alter, exp.Command, exp.Copy, exp.Into, exp.Set, exp.Use,
    exp.Transaction, exp.Commit, exp.Rollback, exp.LockingProperty, exp.Lock,
    exp.GenerateSeries, exp.ExplodingGenerateSeries,  # can produce billions of rows
)


def _ident_is(ident: exp.Expression | None, expected: str) -> bool:
    """Match an identifier the way Postgres does: quoted names are case-sensitive,
    unquoted names are folded to lower case."""
    if not isinstance(ident, exp.Identifier):
        return False
    return ident.name == expected if ident.args.get("quoted") else ident.name.lower() == expected


@dataclass
class ValidationResult:
    ok: bool
    sql: str | None = None    # the SAFE, normalized SQL: the only thing we may execute
    error: str | None = None  # human-readable reason, reusable as feedback for the LLM
    # True for slips the model can fix (unqualified table, stray second statement, broken syntax).
    # False for policy violations (writes, other schemas, dangerous functions): those are never "fixed".
    retryable: bool = False


def _reject(reason: str, retryable: bool = False) -> ValidationResult:
    return ValidationResult(ok=False, error=reason, retryable=retryable)


def validate_sql(raw_sql: str) -> ValidationResult:
    if not raw_sql or not raw_sql.strip():
        return _reject("Empty query.", retryable=True)

    # 1) Must parse as exactly ONE statement.
    try:
        statements = [s for s in sqlglot.parse(raw_sql, read="postgres") if s is not None]
    except SqlglotError as e:
        return _reject(f"SQL could not be parsed: {e}", retryable=True)
    if len(statements) != 1:
        return _reject("Exactly one SQL statement is allowed.", retryable=True)
    tree = statements[0]

    # 2) The statement must be a SELECT (or UNION/INTERSECT/EXCEPT of SELECTs).
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return _reject("Only SELECT queries are allowed.")

    # 3) No write/DDL/session statements anywhere inside the tree.
    for node in tree.walk():
        if isinstance(node, BLOCKED_NODES):
            return _reject(f"Forbidden SQL construct: {type(node).__name__}.")

    # 4) No dangerous functions.
    for func in tree.find_all(exp.Func):
        name = (func.name or "").lower()
        if isinstance(func, exp.Anonymous):
            name = str(func.this).lower()
        if name in BLOCKED_FUNCTIONS:
            return _reject(f"Function not allowed: {name}.")

    # 5) Table allow-list: only analytics_marts.<known table> (or the query's own CTEs).
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return _reject("Table functions and subquery-style table sources are not allowed.")
        if table.args.get("catalog"):
            return _reject("Cross-database references are not allowed.")
        schema_ident = table.args.get("db")
        if schema_ident is None:
            if table.name.lower() in cte_names:
                continue
            return _reject(
                f"Table not allowed: {table.name}. Use {ALLOWED_SCHEMA}.<table> from the schema.",
                retryable=True,  # a missing schema prefix is a slip, not an attack
            )
        if not _ident_is(schema_ident, ALLOWED_SCHEMA) or not any(
            _ident_is(table.this, t) for t in ALLOWED_TABLES
        ):
            return _reject(
                f"Table not allowed: {schema_ident.name}.{table.name}. "
                f"Use only {ALLOWED_SCHEMA}.<table> from the schema."
            )

    # 6) Force a LIMIT on the outermost query (add it, or clamp a larger one).
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(MAX_ROWS)
    else:
        value = limit.expression
        if not (isinstance(value, exp.Literal) and value.is_int and int(value.name) <= MAX_ROWS):
            tree = tree.limit(MAX_ROWS)

    # 7) Return the NORMALIZED SQL: we execute what we validated, never the raw string.
    return ValidationResult(ok=True, sql=tree.sql(dialect="postgres"))