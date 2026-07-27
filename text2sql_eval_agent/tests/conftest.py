"""Shared fixtures for tests that need a live Postgres instance.

Connection settings come from PG* environment variables (falling back to
the same defaults used in examples/demo.py) so CI/dev environments can
point this at whatever Postgres instance they have running. Any DB-backed
test is skipped outright if that instance is unreachable, since EX and
Test-Suite EX inherently require a real database to execute SQL against.
"""

from __future__ import annotations

import os

import pytest

from text2sql_eval.db import PostgresRunner

TEST_SCHEMA = "pytest_company"


def _make_runner() -> PostgresRunner:
    return PostgresRunner(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        dbname=os.environ.get("PGDATABASE", "text2sql_eval_test"),
    )


@pytest.fixture(scope="session")
def pg_runner():
    runner = _make_runner()
    try:
        runner.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Postgres instance available for EX/Test-Suite EX tests: {exc}")
    yield runner
    runner.close()


@pytest.fixture(scope="session")
def company_schema(pg_runner: PostgresRunner):
    """(Re)creates a small employees/departments schema used by execution tests."""
    pg_runner.execute_ddl(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
    pg_runner.execute_ddl(f'CREATE SCHEMA "{TEST_SCHEMA}"')
    pg_runner.execute_ddl(
        f"""
        CREATE TABLE "{TEST_SCHEMA}".departments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        )
        """
    )
    pg_runner.execute_ddl(
        f"""
        CREATE TABLE "{TEST_SCHEMA}".employees (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            department_id INTEGER NOT NULL REFERENCES "{TEST_SCHEMA}".departments(id),
            salary INTEGER NOT NULL,
            hire_year INTEGER NOT NULL
        )
        """
    )
    pg_runner.execute_ddl(
        f"""INSERT INTO "{TEST_SCHEMA}".departments (id, name) VALUES
            (1, 'Engineering'), (2, 'Sales'), (3, 'Marketing')"""
    )
    pg_runner.execute_ddl(
        f"""INSERT INTO "{TEST_SCHEMA}".employees (id, name, department_id, salary, hire_year) VALUES
            (1, 'Alice', 1, 7000, 2019),
            (2, 'Bob', 1, 6000, 2020),
            (3, 'Carol', 1, 4999, 2021),
            (4, 'Dave', 2, 5001, 2018),
            (5, 'Eve', 2, 4998, 2022),
            (6, 'Frank', 2, 3000, 2023),
            (7, 'Grace', 3, 5500, 2017),
            (8, 'Heidi', 3, 4500, 2021),
            (9, 'Ivan', 3, 4997, 2020),
            (10, 'Judy', 1, 8000, 2016)"""
    )
    yield TEST_SCHEMA
    pg_runner.execute_ddl(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
