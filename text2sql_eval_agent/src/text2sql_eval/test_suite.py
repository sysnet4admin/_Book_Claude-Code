"""Test-Suite Execution Accuracy.

Plain EX has a well-known blind spot: two SQL queries that are semantically
different can still return the same result on one particular database
instance by coincidence (e.g. a predicted query joins on the wrong column,
or forgets a filter that happens not to remove any rows in this dataset).
*Test-Suite Execution Accuracy for Semantic Parsing* (Zhong, Yu & Klein,
EMNLP 2020) addresses this by generating several additional "neighboring"
database instances - the same schema, but with perturbed data - and only
counting a predicted query correct if it agrees with gold on *every*
instance. A predicted query that is wrong for a reason the original data
happened to mask gets caught on at least one perturbed instance.

This module implements a lightweight version of that idea against
Postgres, using cheap-to-generate perturbations rather than the paper's
full randomized-database generator:

  * shuffling a column's values across rows (catches "joined/selected the
    wrong column" bugs that coincidentally line up on the original data);
  * injecting NULLs into non-key columns (catches missing/incorrect NULL
    handling);
  * duplicating a few rows (catches missing DISTINCT / incorrect dedup);
  * jittering numeric columns by a small integer offset (catches
    off-by-one or wrong-comparison-operator bugs in filters).

Primary key and foreign key columns (discovered from the source schema's
constraints) are never perturbed, so joins that are supposed to work keep
working - perturbation targets the *data*, not the relational structure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from text2sql_eval.db import PostgresRunner
from text2sql_eval.execution import evaluate_execution

_IDENT = str  # readability alias; real validation happens via psycopg.sql.Identifier


@dataclass
class TestSuiteConfig:
    num_instances: int = 5
    seed: int = 0
    null_injection_rate: float = 0.1
    duplicate_row_count: int = 2
    numeric_jitter_max: int = 3


@dataclass
class TestSuiteInstanceResult:
    schema_name: str
    match: bool | None
    detail: str


@dataclass
class TestSuiteEvalResult:
    passed_all: bool | None
    pass_rate: float | None
    instances: list[TestSuiteInstanceResult] = field(default_factory=list)
    gold_error: str | None = None


class TestSuiteBuilder:
    """Creates and tears down perturbed clones of a source Postgres schema."""

    def __init__(
        self,
        runner: PostgresRunner,
        source_schema: str,
        config: TestSuiteConfig | None = None,
        name_prefix: str = "ts",
    ) -> None:
        self.runner = runner
        self.source_schema = source_schema
        self.config = config or TestSuiteConfig()
        self.name_prefix = name_prefix

    def _tables(self) -> list[str]:
        outcome = self.runner.run(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            params=(self.source_schema,),
        )
        if not outcome.ok or outcome.rows is None:
            return []
        return [row[0] for row in outcome.rows]

    def _key_columns(self, table: str) -> set[str]:
        outcome = self.runner.run(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = %s AND tc.table_name = %s
              AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
            """,
            params=(self.source_schema, table),
        )
        if not outcome.ok or outcome.rows is None:
            return set()
        return {row[0] for row in outcome.rows}

    def _columns(self, table: str) -> list[tuple[str, str]]:
        outcome = self.runner.run(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            params=(self.source_schema, table),
        )
        if not outcome.ok or outcome.rows is None:
            return []
        return [(row[0], row[1]) for row in outcome.rows]

    def _quote(self, ident: _IDENT) -> str:
        return '"' + ident.replace('"', '""') + '"'

    def _qualified(self, schema: str, table: str) -> str:
        return f"{self._quote(schema)}.{self._quote(table)}"

    def _copy_table(self, schema_name: str, table: str) -> None:
        self.runner.execute_ddl(
            f"CREATE TABLE {self._qualified(schema_name, table)} AS "
            f"TABLE {self._qualified(self.source_schema, table)}"
        )

    def _perturb_table(self, schema_name: str, table: str, rng: random.Random) -> None:
        qualified = self._qualified(schema_name, table)
        key_cols = self._key_columns(table)
        columns = self._columns(table)
        numeric_types = {"integer", "bigint", "smallint", "numeric", "real", "double precision"}

        for col_name, data_type in columns:
            if col_name in key_cols:
                continue
            col = self._quote(col_name)

            if rng.random() < 0.6:
                # Shuffle this column's values across rows: catches predicted
                # queries that reference the wrong (but type-compatible)
                # column, which would otherwise coincidentally match gold on
                # unperturbed data.
                self.runner.execute_ddl(
                    f"""
                    WITH src AS (
                        SELECT ctid, {col} AS val, row_number() OVER () AS rn
                        FROM {qualified}
                    ), shuffled AS (
                        SELECT ctid AS target_ctid, row_number() OVER (ORDER BY random()) AS rn
                        FROM {qualified}
                    )
                    UPDATE {qualified} t
                    SET {col} = src.val
                    FROM shuffled JOIN src ON shuffled.rn = src.rn
                    WHERE t.ctid = shuffled.target_ctid
                    """
                )

            if self.config.null_injection_rate > 0 and rng.random() < 0.5:
                rate = self.config.null_injection_rate
                self.runner.execute_ddl(
                    f"""
                    UPDATE {qualified}
                    SET {col} = NULL
                    WHERE ctid IN (
                        SELECT ctid FROM {qualified}
                        ORDER BY random()
                        LIMIT GREATEST(1, (SELECT (count(*) * {rate})::int FROM {qualified}))
                    )
                    """
                )

            if data_type in numeric_types and rng.random() < 0.5:
                jitter = rng.randint(1, max(1, self.config.numeric_jitter_max))
                self.runner.execute_ddl(
                    f"UPDATE {qualified} SET {col} = {col} + {jitter} "
                    f"WHERE ctid IN (SELECT ctid FROM {qualified} ORDER BY random() LIMIT "
                    f"GREATEST(1, (SELECT count(*)/3 FROM {qualified})))"
                )

        if self.config.duplicate_row_count > 0:
            self.runner.execute_ddl(
                f"INSERT INTO {qualified} SELECT * FROM {qualified} "
                f"ORDER BY random() LIMIT {self.config.duplicate_row_count}"
            )

    def build(self, case_id: str) -> list[str]:
        """Create `num_instances` perturbed clones; return their schema names."""
        tables = self._tables()
        schema_names = []
        for i in range(self.config.num_instances):
            schema_name = f"{self.name_prefix}_{case_id}_{i}"
            rng = random.Random(f"{self.config.seed}:{case_id}:{i}")
            self.runner.execute_ddl(
                f"CREATE SCHEMA IF NOT EXISTS {self._quote(schema_name)}"
            )
            for table in tables:
                self._copy_table(schema_name, table)
                self._perturb_table(schema_name, table, rng)
            schema_names.append(schema_name)
        return schema_names

    def drop_all(self, schema_names: list[str]) -> None:
        for name in schema_names:
            self.runner.drop_schema(name)


def evaluate_test_suite(
    runner: PostgresRunner,
    source_schema: str,
    case_id: str,
    gold_sql: str,
    pred_sql: str,
    *,
    config: TestSuiteConfig | None = None,
    order_matters: bool | None = None,
    dialect: str = "postgres",
) -> TestSuiteEvalResult:
    """Run gold/pred against `config.num_instances` perturbed clones of
    `source_schema` and require agreement on all of them.

    A predicted query only counts as passing test-suite EX if it matches
    gold's result on every perturbed instance, not just the original
    database - this is the paper's core mechanism for rejecting queries
    that are "accidentally right" on one specific dataset.
    """
    builder = TestSuiteBuilder(runner, source_schema, config)
    schema_names = builder.build(case_id)
    try:
        instance_results: list[TestSuiteInstanceResult] = []
        for schema_name in schema_names:
            match, detail, gold_outcome, _pred_outcome = evaluate_execution(
                runner,
                gold_sql,
                pred_sql,
                db_name=schema_name,
                order_matters=order_matters,
                dialect=dialect,
            )
            if match is None:
                return TestSuiteEvalResult(
                    passed_all=None,
                    pass_rate=None,
                    instances=instance_results,
                    gold_error=f"gold SQL failed on test-suite instance {schema_name}: {gold_outcome.error}",
                )
            instance_results.append(TestSuiteInstanceResult(schema_name, match, detail))

        passed = sum(1 for r in instance_results if r.match)
        total = len(instance_results)
        return TestSuiteEvalResult(
            passed_all=(passed == total) if total else None,
            pass_rate=(passed / total) if total else None,
            instances=instance_results,
        )
    finally:
        builder.drop_all(schema_names)
