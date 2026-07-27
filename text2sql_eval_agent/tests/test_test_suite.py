from text2sql_eval.test_suite import TestSuiteConfig, evaluate_test_suite


def test_test_suite_ex_agrees_with_plain_ex_on_a_correct_query(pg_runner, company_schema):
    gold = "SELECT name FROM employees WHERE hire_year < 2020"
    pred = "select name from employees where hire_year < 2020"
    result = evaluate_test_suite(
        pg_runner, company_schema, "correct_query", gold, pred, config=TestSuiteConfig(num_instances=5)
    )
    assert result.passed_all is True
    assert result.pass_rate == 1.0


def test_test_suite_ex_catches_a_bug_masked_by_coincidence(pg_runner, company_schema):
    # On the seed data, employee id order happens to match salary rank
    # within department 1, so a predicted query that orders by `id`
    # instead of `salary` returns the same top row as gold on the
    # original, unperturbed database.
    gold = "SELECT name FROM employees WHERE department_id = 1 ORDER BY salary DESC LIMIT 1"
    pred = "SELECT name FROM employees WHERE department_id = 1 ORDER BY id DESC LIMIT 1"

    result = evaluate_test_suite(
        pg_runner, company_schema, "masked_bug", gold, pred, config=TestSuiteConfig(num_instances=10)
    )
    assert result.passed_all is False
    assert result.pass_rate is not None and result.pass_rate < 1.0


def test_test_suite_ex_reports_gold_error(pg_runner, company_schema):
    result = evaluate_test_suite(
        pg_runner,
        company_schema,
        "bad_gold",
        "SELECT name FROM nonexistent_table",
        "SELECT name FROM employees",
        config=TestSuiteConfig(num_instances=2),
    )
    assert result.passed_all is None
    assert result.gold_error is not None
