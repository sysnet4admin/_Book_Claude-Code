from text2sql_eval.evaluator import Evaluator, EvaluatorConfig
from text2sql_eval.models import EvalCase


def test_evaluator_em_only_needs_no_database():
    evaluator = Evaluator(runner=None, config=EvaluatorConfig(run_execution=False, run_test_suite=False))
    case = EvalCase(case_id="c1", gold_sql="SELECT a FROM t", pred_sql="select a from t")
    result = evaluator.evaluate_case(case)
    assert result.em is True
    assert result.ex is None
    assert result.test_suite_ex is None


def test_evaluator_combines_em_and_ex(pg_runner, company_schema):
    evaluator = Evaluator(runner=pg_runner, config=EvaluatorConfig(run_execution=True, run_test_suite=False))
    cases = [
        EvalCase(
            case_id="match",
            db_name=company_schema,
            gold_sql="SELECT name FROM employees WHERE id = 1",
            pred_sql="select name from employees where id = 1",
        ),
        EvalCase(
            case_id="mismatch",
            db_name=company_schema,
            gold_sql="SELECT name FROM employees WHERE hire_year < 2020",
            pred_sql="SELECT name FROM employees WHERE hire_year > 2020",
        ),
    ]
    report = evaluator.evaluate(cases)
    assert report.total == 2
    by_id = {r.case_id: r for r in report.results}
    assert by_id["match"].em is True and by_id["match"].ex is True
    assert by_id["mismatch"].em is False and by_id["mismatch"].ex is False
    assert report.em_accuracy == 0.5
    assert report.ex_accuracy == 0.5
    assert report.test_suite_ex_accuracy is None


def test_evaluator_raises_without_runner_when_ex_requested():
    evaluator = Evaluator(runner=None, config=EvaluatorConfig(run_execution=True))
    case = EvalCase(case_id="c1", db_name="whatever", gold_sql="SELECT a FROM t", pred_sql="SELECT a FROM t")
    try:
        evaluator.evaluate_case(case)
        assert False, "expected ValueError"
    except ValueError:
        pass
