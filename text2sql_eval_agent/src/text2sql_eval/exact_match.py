"""Exact Match (EM) via sqlglot AST normalization.

Text-level string diffing is a poor EM metric because two SQL strings can
be semantically identical while differing in whitespace, keyword casing,
table-alias names, or the order of commutative clauses (``a AND b`` vs
``b AND a``). This module parses both queries with sqlglot, rewrites each
AST into a canonical form, and compares the canonical output strings.

Canonicalization applied:
  * identifiers are lowercased (unquoted identifiers are case-insensitive
    in Postgres) via ``sqlglot.optimizer.normalize_identifiers``;
  * table aliases are renamed to positional placeholders (``__t0``,
    ``__t1``, ...) per scope, via ``sqlglot.optimizer.scope.traverse_scope``,
    so ``FROM users AS u`` vs ``FROM users AS usr`` canonicalize the same;
  * commutative boolean chains (``AND``/``OR``) and ``IN (...)`` literal
    lists are sorted, since operand order never changes their meaning;
  * ``GROUP BY`` expressions are sorted (grouping order doesn't change the
    result set);
  * numeric literals are normalized (``2.0`` -> ``2``).

What is intentionally left order-sensitive: ``ORDER BY`` (defines output
row order) and, by default, the ``SELECT`` list order can optionally be
ignored via ``ignore_select_order`` (default True), mirroring how
benchmarks like Spider score column order in the SELECT list leniently
since :mod:`text2sql_eval.execution` already tolerates it during EX.

Known limitations: alias canonicalization is scope-aware (via
``traverse_scope``) but does not attempt cross-query join-order
normalization (e.g. swapping the order of two INNER JOINs), and does not
require/validate a schema, so ambiguous unqualified columns are compared
as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import sqlglot
import sqlglot.expressions as exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope


@dataclass
class ExactMatchResult:
    match: bool
    gold_canonical: str | None
    pred_canonical: str | None
    error: str | None = None


def _flatten_binary(node: exp.Expression, op_type: type[exp.Expression]) -> list[exp.Expression]:
    if isinstance(node, op_type):
        return _flatten_binary(node.this, op_type) + _flatten_binary(node.expression, op_type)
    return [node]


def _rebuild_binary_chain(
    op_type: type[exp.Expression], operands: list[exp.Expression]
) -> exp.Expression:
    node = operands[0]
    for operand in operands[1:]:
        node = op_type(this=node, expression=operand)
    return node


def _sort_commutative_chains(tree: exp.Expression) -> exp.Expression:
    for node in list(tree.find_all((exp.And, exp.Or))):
        if isinstance(node.parent, type(node)):
            continue  # interior node of a chain already handled at its top
        op_type = type(node)
        operands = _flatten_binary(node, op_type)
        operands.sort(key=lambda o: o.sql())
        node.replace(_rebuild_binary_chain(op_type, operands))
    return tree


def _sort_in_lists(tree: exp.Expression) -> exp.Expression:
    for node in tree.find_all(exp.In):
        values = node.args.get("expressions")
        if values:
            node.set("expressions", sorted(values, key=lambda o: o.sql()))
    return tree


def _sort_group_by(tree: exp.Expression) -> exp.Expression:
    for group in tree.find_all(exp.Group):
        values = group.args.get("expressions")
        if values:
            group.set("expressions", sorted(values, key=lambda o: o.sql()))
    return tree


def _sort_select_list(tree: exp.Expression) -> exp.Expression:
    for select in tree.find_all(exp.Select):
        values = select.args.get("expressions")
        if values:
            select.set("expressions", sorted(values, key=lambda o: o.sql()))
    return tree


def _normalize_numeric_literals(tree: exp.Expression) -> exp.Expression:
    for lit in tree.find_all(exp.Literal):
        if not lit.is_number:
            continue
        try:
            as_decimal = Decimal(lit.this)
        except (InvalidOperation, ValueError):
            continue
        normalized = format(as_decimal.normalize(), "f")
        if normalized in ("", "-0"):
            normalized = "0"
        lit.set("this", normalized)
    return tree


def _canonicalize_table_aliases(tree: exp.Expression) -> exp.Expression:
    for scope in traverse_scope(tree):
        rename_map: dict[str, str] = {}
        for idx, table in enumerate(scope.tables):
            rename_map[table.alias_or_name] = f"__t{idx}"
        for table in scope.tables:
            canonical = rename_map[table.alias_or_name]
            table.set("alias", exp.TableAlias(this=exp.to_identifier(canonical)))
        for column in scope.columns:
            if column.table and column.table in rename_map:
                column.set("table", exp.to_identifier(rename_map[column.table]))
    return tree


def normalize_sql(
    sql: str,
    dialect: str = "postgres",
    *,
    ignore_select_order: bool = True,
) -> str:
    """Parse `sql` and render a canonical string suitable for EM comparison."""
    tree = sqlglot.parse_one(sql, read=dialect)
    tree = normalize_identifiers(tree)
    tree = _canonicalize_table_aliases(tree)
    tree = _normalize_numeric_literals(tree)
    tree = _sort_commutative_chains(tree)
    tree = _sort_in_lists(tree)
    tree = _sort_group_by(tree)
    if ignore_select_order:
        tree = _sort_select_list(tree)
    return tree.sql(dialect=dialect, pretty=False)


def is_exact_match(
    gold_sql: str,
    pred_sql: str,
    dialect: str = "postgres",
    *,
    ignore_select_order: bool = True,
) -> ExactMatchResult:
    """Compare gold and predicted SQL by canonical AST form.

    Parse failures on either side are reported via `error` rather than
    raised, since a predicted query that doesn't even parse is simply not
    an exact match.
    """
    try:
        gold_canon = normalize_sql(gold_sql, dialect, ignore_select_order=ignore_select_order)
    except Exception as exc:  # noqa: BLE001
        return ExactMatchResult(False, None, None, error=f"gold SQL failed to parse: {exc}")
    try:
        pred_canon = normalize_sql(pred_sql, dialect, ignore_select_order=ignore_select_order)
    except Exception as exc:  # noqa: BLE001
        return ExactMatchResult(False, gold_canon, None, error=f"predicted SQL failed to parse: {exc}")

    return ExactMatchResult(gold_canon == pred_canon, gold_canon, pred_canon)
