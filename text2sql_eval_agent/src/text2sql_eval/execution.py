"""Execution Accuracy (EX).

Runs gold and predicted SQL against a live database and compares their
result sets rather than their text, so a predicted query that is written
differently from gold but returns the same data still counts as correct.

The comparison algorithm follows the methodology from *Test-Suite Execution
Accuracy for Semantic Parsing* (Zhong, Yu & Klein, EMNLP 2020): because a
predicted query may select the same columns as gold in a different order
(e.g. ``SELECT b, a`` vs ``SELECT a, b``), we search over column
permutations of the predicted result and accept a match if *any*
permutation makes the two result sets equal - as a bag (duplicates matter)
when the gold query has no dedup guarantee, or an ordered sequence when the
gold query has a top-level ``ORDER BY``.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Sequence

import sqlglot

from text2sql_eval.db import PostgresRunner, QueryOutcome

Row = tuple[Any, ...]

# Beyond this many columns, permutation search (n!) is capped: only swap
# columns whose value sets are identical, which is what the search already
# restricts to, but we also hard-cap to avoid pathological blowups.
_MAX_COLUMNS_FOR_FULL_PERMUTATION = 3
_MAX_PERMUTATIONS_TRIED = 5000


@dataclass
class ExecutionComparison:
    match: bool
    reason: str
    gold_row_count: int | None = None
    pred_row_count: int | None = None


def has_top_level_order_by(sql: str, dialect: str = "postgres") -> bool:
    """True if the outermost SELECT of `sql` has an ORDER BY clause.

    Row order is only part of the intended semantics when gold explicitly
    asks for it; otherwise two result sets with the same rows in a
    different order are equivalent.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - unparsable SQL just means "assume unordered"
        return False
    return tree.args.get("order") is not None


def _sort_key(value: Any) -> str:
    # Sorting mixed types (None, int, str, Decimal, datetime, ...) directly
    # raises TypeError in Python 3, so sort by a stable string projection
    # that still separates values of different types/values consistently.
    return f"{type(value).__name__}:{value!r}"


def _unorder_row(row: Row) -> Row:
    """Sort a row's cell values so column order within the row can't matter."""
    return tuple(sorted(row, key=_sort_key))


def _quick_reject(rows1: list[Row], rows2: list[Row], order_matters: bool) -> bool:
    """Cheap pre-check: compare rows with cell order ignored, before the
    (more expensive) column-permutation search."""
    unordered1 = [_unorder_row(r) for r in rows1]
    unordered2 = [_unorder_row(r) for r in rows2]
    if order_matters:
        return unordered1 == unordered2
    return _as_multiset(unordered1) == _as_multiset(unordered2)


def _as_multiset(rows: list[Row]) -> dict[Row, int]:
    counts: dict[Row, int] = {}
    for row in rows:
        counts[row] = counts.get(row, 0) + 1
    return counts


def _candidate_permutations(gold_rows: list[Row], num_cols: int) -> list[tuple[int, ...]]:
    """Column index permutations worth trying against gold.

    Two columns are only worth swapping if they contain the exact same set
    of values in gold's own result (otherwise permuting can never produce a
    match), which keeps the search cheap even when `num_cols` is large.
    """
    if num_cols <= _MAX_COLUMNS_FOR_FULL_PERMUTATION:
        return list(itertools.permutations(range(num_cols)))

    value_sets = [frozenset(row[i] for row in gold_rows) for i in range(num_cols)]
    allowed_targets = [
        {j for j in range(num_cols) if value_sets[i] == value_sets[j]} for i in range(num_cols)
    ]
    candidates: list[tuple[int, ...]] = []
    for perm in itertools.product(*allowed_targets):
        if len(set(perm)) == num_cols and len(candidates) < _MAX_PERMUTATIONS_TRIED:
            candidates.append(perm)
    return candidates or [tuple(range(num_cols))]


def _permute_row(row: Row, perm: Sequence[int]) -> Row:
    return tuple(row[i] for i in perm)


def result_eq(
    gold_rows: list[Row], pred_rows: list[Row], *, order_matters: bool
) -> ExecutionComparison:
    """Compare two result sets, tolerant to column-order differences.

    `order_matters` should reflect whether gold's SQL has a top-level
    ORDER BY (see `has_top_level_order_by`); when False, row order is
    ignored but row multiplicity (duplicates) is still respected, matching
    the "bag semantics" the test-suite paper argues for over naive set
    comparison.
    """
    if len(gold_rows) != len(pred_rows):
        return ExecutionComparison(
            False,
            f"row count differs: gold={len(gold_rows)} pred={len(pred_rows)}",
            len(gold_rows),
            len(pred_rows),
        )
    if len(gold_rows) == 0:
        return ExecutionComparison(True, "both empty", 0, 0)

    num_cols = len(gold_rows[0])
    if len(pred_rows[0]) != num_cols:
        return ExecutionComparison(
            False,
            f"column count differs: gold={num_cols} pred={len(pred_rows[0])}",
            len(gold_rows),
            len(pred_rows),
        )

    if not _quick_reject(gold_rows, pred_rows, order_matters):
        return ExecutionComparison(
            False, "no column permutation possible: value multisets differ",
            len(gold_rows), len(pred_rows),
        )

    for perm in _candidate_permutations(gold_rows, num_cols):
        permuted_pred = [_permute_row(row, perm) for row in pred_rows]
        if order_matters:
            if permuted_pred == gold_rows:
                return ExecutionComparison(True, f"matched with column order {perm}",
                                            len(gold_rows), len(pred_rows))
        else:
            if _as_multiset(permuted_pred) == _as_multiset(gold_rows):
                return ExecutionComparison(True, f"matched with column order {perm}",
                                            len(gold_rows), len(pred_rows))

    return ExecutionComparison(
        False, "no column permutation of predicted results matches gold",
        len(gold_rows), len(pred_rows),
    )


def evaluate_execution(
    runner: PostgresRunner,
    gold_sql: str,
    pred_sql: str,
    *,
    db_name: str | None = None,
    order_matters: bool | None = None,
    dialect: str = "postgres",
) -> tuple[bool | None, str, QueryOutcome, QueryOutcome]:
    """Run gold/pred SQL and compare their results.

    Returns (ex_or_none, detail, gold_outcome, pred_outcome). `ex` is None
    when the gold query itself fails to execute (a broken benchmark item,
    not a predicted-query mistake); it is False when only the predicted
    query fails.
    """
    search_path = db_name
    gold_outcome = runner.run(gold_sql, search_path=search_path)
    pred_outcome = runner.run(pred_sql, search_path=search_path)

    if not gold_outcome.ok:
        return None, f"gold SQL failed to execute: {gold_outcome.error}", gold_outcome, pred_outcome
    if not pred_outcome.ok:
        return False, f"predicted SQL failed to execute: {pred_outcome.error}", gold_outcome, pred_outcome

    resolved_order_matters = (
        order_matters if order_matters is not None else has_top_level_order_by(gold_sql, dialect)
    )
    comparison = result_eq(
        gold_outcome.rows or [], pred_outcome.rows or [], order_matters=resolved_order_matters
    )
    return comparison.match, comparison.reason, gold_outcome, pred_outcome
