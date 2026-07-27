from text2sql_eval.execution import evaluate_execution, has_top_level_order_by, result_eq


def test_has_top_level_order_by():
    assert has_top_level_order_by("SELECT a FROM t ORDER BY a") is True
    assert has_top_level_order_by("SELECT a FROM t") is False


def test_result_eq_ignores_column_order():
    comparison = result_eq([(1, "a"), (2, "b")], [("a", 1), ("b", 2)], order_matters=False)
    assert comparison.match is True


def test_result_eq_bag_semantics_respects_duplicates():
    comparison = result_eq([(1,), (1,), (2,)], [(1,), (2,)], order_matters=False)
    assert comparison.match is False


def test_result_eq_order_matters():
    assert result_eq([(1,), (2,)], [(2,), (1,)], order_matters=True).match is False
    assert result_eq([(1,), (2,)], [(1,), (2,)], order_matters=True).match is True


def test_evaluate_execution_matches_equivalent_queries(pg_runner, company_schema):
    gold = "SELECT name FROM employees WHERE department_id = 1 ORDER BY name"
    pred = "select name from employees where department_id = 1 order by name"
    match, detail, gold_outcome, pred_outcome = evaluate_execution(
        pg_runner, gold, pred, db_name=company_schema
    )
    assert match is True, detail
    assert gold_outcome.ok and pred_outcome.ok


def test_evaluate_execution_detects_mismatch(pg_runner, company_schema):
    gold = "SELECT name FROM employees WHERE hire_year < 2020"
    pred = "SELECT name FROM employees WHERE hire_year > 2020"
    match, _detail, _g, _p = evaluate_execution(pg_runner, gold, pred, db_name=company_schema)
    assert match is False


def test_evaluate_execution_tolerates_column_order_difference(pg_runner, company_schema):
    gold = "SELECT name, salary FROM employees WHERE id = 1"
    pred = "SELECT salary, name FROM employees WHERE id = 1"
    match, detail, _g, _p = evaluate_execution(pg_runner, gold, pred, db_name=company_schema)
    assert match is True, detail


def test_evaluate_execution_reports_pred_sql_error(pg_runner, company_schema):
    gold = "SELECT name FROM employees WHERE id = 1"
    pred = "SELECT name FROM nonexistent_table"
    match, detail, gold_outcome, pred_outcome = evaluate_execution(
        pg_runner, gold, pred, db_name=company_schema
    )
    assert match is False
    assert gold_outcome.ok
    assert not pred_outcome.ok
    assert "nonexistent_table" in detail or not pred_outcome.ok


def test_evaluate_execution_reports_gold_sql_error(pg_runner, company_schema):
    gold = "SELECT name FROM nonexistent_table"
    pred = "SELECT name FROM employees"
    match, _detail, gold_outcome, _pred_outcome = evaluate_execution(
        pg_runner, gold, pred, db_name=company_schema
    )
    assert match is None
    assert not gold_outcome.ok
