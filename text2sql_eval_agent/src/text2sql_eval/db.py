"""Thin PostgreSQL execution layer used by the EX and Test-Suite EX metrics.

Both metrics only need "run this SQL, get columns + rows back, tell me if it
errored" - so this module stays intentionally small instead of wrapping a
full ORM.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

import psycopg
import psycopg.rows
import psycopg.sql


@dataclass
class QueryOutcome:
    """Result of running one SQL statement, or the error it raised."""

    columns: list[str] | None = None
    rows: list[tuple] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class PostgresRunner:
    """Executes read-only SQL against a Postgres database and returns rows.

    A single connection is reused across calls (`autocommit=True`), and a
    dedicated cursor/transaction is used per query so a failing statement
    (e.g. the model's predicted SQL is invalid) doesn't poison the
    connection for the next query.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "",
        dbname: str = "postgres",
        statement_timeout_ms: int = 15_000,
    ) -> None:
        self._dsn = dsn or psycopg.conninfo.make_conninfo(
            host=host, port=port, user=user, password=password, dbname=dbname
        )
        self._statement_timeout_ms = statement_timeout_ms
        self._conn: psycopg.Connection | None = None

    def connect(self) -> None:
        if self._conn is not None and not self._conn.closed:
            return
        self._conn = psycopg.connect(self._dsn, autocommit=True)

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> "PostgresRunner":
        self.connect()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def run(
        self,
        sql: str,
        search_path: str | None = None,
        params: Sequence[Any] | None = None,
    ) -> QueryOutcome:
        """Execute a single SQL statement and fetch all rows.

        Runs inside its own subtransaction (savepoint) so a syntax/runtime
        error in `sql` is isolated and rolled back without dropping the
        outer autocommit connection.
        """
        self.connect()
        assert self._conn is not None
        try:
            with self._conn.transaction():
                with self._conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
                    if search_path:
                        set_path = psycopg.sql.SQL("SET LOCAL search_path TO {}").format(
                            psycopg.sql.Identifier(search_path)
                        )
                        cur.execute(set_path)
                    cur.execute(f"SET LOCAL statement_timeout = {self._statement_timeout_ms}")
                    cur.execute(sql, params)
                    if cur.description is None:
                        return QueryOutcome(columns=[], rows=[])
                    columns = [d.name for d in cur.description]
                    rows = cur.fetchall()
                    return QueryOutcome(columns=columns, rows=rows)
        except Exception as exc:  # noqa: BLE001 - invalid predicted SQL is expected
            return QueryOutcome(error=f"{type(exc).__name__}: {exc}")

    def execute_ddl(self, sql: str | psycopg.sql.Composable, params: Sequence[Any] | None = None) -> None:
        """Run DDL/DML that must succeed (schema setup, perturbation seeding)."""
        self.connect()
        assert self._conn is not None
        with self._conn.cursor() as cur:
            cur.execute(sql, params)

    def create_schema(self, schema_name: str) -> None:
        self.execute_ddl(
            psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(psycopg.sql.Identifier(schema_name))
        )

    def drop_schema(self, schema_name: str) -> None:
        self.execute_ddl(
            psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(psycopg.sql.Identifier(schema_name))
        )

    @contextmanager
    def temp_schema(self, schema_name: str) -> Iterator[str]:
        """Create `schema_name`, yield it, then drop it (even on error)."""
        self.execute_ddl(
            psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(psycopg.sql.Identifier(schema_name))
        )
        try:
            yield schema_name
        finally:
            self.execute_ddl(
                psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    psycopg.sql.Identifier(schema_name)
                )
            )
