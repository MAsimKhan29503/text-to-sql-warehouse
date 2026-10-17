import pytest

from t2s.guardrails import validate_sql

ALLOWED = [
    "select repo_id from analytics_marts.dim_repo limit 5",
    "select r.repo_name, sum(d.stars) as s "
    "from analytics_marts.fct_daily_repo_activity d "
    "join analytics_marts.dim_repo r on r.repo_id = d.repo_id "
    "group by 1 order by 2 desc limit 5;",
    "with m as (select max(event_date) as d from analytics_marts.fct_daily_repo_activity) "
    "select d from m",
    "select repo_id from analytics_marts.dim_repo "
    "union select repo_id from analytics_marts.fct_events",
    'select * from "analytics_marts"."dim_repo"',
]

BLOCKED = [
    ("", "empty"),
    ("hello world", "not sql"),
    ("select 1; drop table analytics_marts.dim_repo", "stacked statements"),
    ("drop table analytics_marts.dim_repo", "drop"),
    ("insert into analytics_marts.dim_repo (repo_id) values (1)", "insert"),
    ("update analytics_marts.dim_repo set repo_name = 'x'", "update"),
    ("delete from analytics_marts.fct_events", "delete"),
    ("truncate analytics_marts.fct_events", "truncate"),
    ("create table analytics_marts.t as select 1", "create"),
    ("with x as (delete from analytics_marts.dim_repo returning *) select * from x", "write inside cte"),
    ("select * into newt from analytics_marts.dim_repo", "select into"),
    ("select * from analytics_marts.dim_repo for update", "row locks"),
    ("copy analytics_marts.dim_repo to '/tmp/x'", "copy"),
    ("set statement_timeout = 0", "set"),
    ("select pg_sleep(60)", "sleep"),
    ("select pg_catalog.pg_sleep(60)", "qualified sleep"),
    ("select set_config('statement_timeout', '0', false)", "set_config"),
    ("select pg_read_file('/etc/passwd')", "file read"),
    ("select generate_series(1, 1000000000)", "row bomb in select list"),
    ("select * from generate_series(1, 1000000000)", "row bomb in from"),
    ("select * from raw.gh_events", "other schema"),
    ("select * from dim_repo", "unqualified table"),
    ("select * from pg_catalog.pg_tables", "catalog"),
    ("select * from information_schema.tables", "information_schema"),
    ("select * from other.analytics_marts.dim_repo", "other database"),
    ('select * from "Analytics_Marts".dim_repo', "case-sensitive quoted schema"),
    ("select * from (select * from raw.gh_events) t", "forbidden table in subquery"),
    ("select * from analytics_marts.dim_repo, pg_read_file('/etc/passwd')", "function as source"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_queries_pass_and_get_a_limit(sql):
    result = validate_sql(sql)
    assert result.ok, result.error
    assert "LIMIT" in result.sql.upper()


@pytest.mark.parametrize("sql,why", BLOCKED)
def test_dangerous_queries_are_blocked(sql, why):
    result = validate_sql(sql)
    assert not result.ok, f"should have been blocked: {why}"
    assert result.error and result.sql is None


def test_missing_limit_is_added():
    result = validate_sql("select repo_id from analytics_marts.dim_repo")
    assert result.sql.endswith("LIMIT 100")


def test_small_limit_is_kept():
    result = validate_sql("select repo_id from analytics_marts.dim_repo limit 5")
    assert result.sql.endswith("LIMIT 5")


@pytest.mark.parametrize("limit", ["100000", "ALL"])
def test_huge_limit_is_clamped(limit):
    result = validate_sql(f"select repo_id from analytics_marts.dim_repo limit {limit}")
    assert result.sql.endswith("LIMIT 100")




FIXABLE_SLIPS = [
    "select * from dim_repo",            # forgot the schema prefix
    "select 1; select 2",                # stray second statement
    "select from where ((",              # broken syntax
    "",
]
POLICY_VIOLATIONS = [
    "drop table analytics_marts.dim_repo",
    "select * from raw.gh_events",
    "select pg_sleep(60)",
    "select * from analytics_marts.dim_repo for update",
    "select * from other.analytics_marts.dim_repo",
    "select * from generate_series(1, 5)",
]


@pytest.mark.parametrize("sql", FIXABLE_SLIPS)
def test_slips_are_marked_retryable(sql):
    result = validate_sql(sql)
    assert not result.ok and result.retryable


@pytest.mark.parametrize("sql", POLICY_VIOLATIONS)
def test_policy_violations_are_not_retryable(sql):
    result = validate_sql(sql)
    assert not result.ok and not result.retryable