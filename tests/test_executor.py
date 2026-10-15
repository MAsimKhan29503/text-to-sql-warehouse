from types import SimpleNamespace

import psycopg
import pytest
from psycopg import errors

import t2s.executor as executor

SETTINGS = SimpleNamespace(pg_host="h", pg_port=1, pg_db="d", pg_ro_user="u", pg_ro_password="p")


@pytest.mark.parametrize(
    "exc,kind",
    [
        (errors.QueryCanceled("slow"), "timeout"),
        (errors.DiskFull("could not resize shared memory segment"), "resources"),
        (errors.OutOfMemory("out of memory"), "resources"),
        (psycopg.OperationalError("connection refused"), "unavailable"),
        (errors.UndefinedColumn('column "x" does not exist'), "database"),
    ],
)
def test_database_errors_are_classified(monkeypatch, exc, kind):
    def boom(**kwargs):
        raise exc

    monkeypatch.setattr(executor, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(executor.psycopg, "connect", boom)
    result = executor.run_query("select 1")
    assert not result.ok and result.error_kind == kind and result.error