"""Evaluator: runs EM, EX, and (optionally) Test-Suite EX for a batch of cases."""

from __future__ import annotations

from dataclasses import dataclass

from text2sql_eval.db import PostgresRunner
from text2sql_eval.exact_match import is_exact_match
from text2sql_eval.execution import evaluate_execution
from text2sql_eval.models import EvalCase, EvalReport, EvalResult
from text2sql_eval.test_suite import TestSuiteConfig, evaluate_test_suite


@dataclass
class EvaluatorConfig:
    dialect: str = "postgres"
    ignore_select_order: bool = True
    run_execution: bool = True
    run_test_suite: bool = False
    test_suite: TestSuiteConfig | None = None


class Evaluator:
    """Evaluates :class:`EvalCase` items with EM, EX, and Test-Suite EX.

    EM (`exact_match.is_exact_match`) never touches a database, so it runs
    even when `runner` is None. EX and Test-Suite EX require a live
    Postgres connection since they execute the SQL.
    """

    def __init__(self, runner: PostgresRunner | None = None, config: EvaluatorConfig | None = None) -> None:
        self.runner = runner
        self.config = config or EvaluatorConfig()

    def evaluate_case(self, case: EvalCase) -> EvalResult:
        em_result = is_exact_match(
            case.gold_sql,
            case.pred_sql,
            self.config.dialect,
            ignore_select_order=self.config.ignore_select_order,
        )
        result = EvalResult(
            case_id=case.case_id,
            em=em_result.match,
            em_detail=em_result.error,
        )

        needs_db = self.config.run_execution or self.config.run_test_suite
        if not needs_db:
            return result
        if self.runner is None:
            raise ValueError(
                f"case {case.case_id}: EX/Test-Suite EX requested but no PostgresRunner was provided"
            )
        if not case.db_name:
            raise ValueError(f"case {case.case_id}: EX/Test-Suite EX requires case.db_name (schema)")

        if self.config.run_execution:
            ex_match, ex_detail, gold_outcome, pred_outcome = evaluate_execution(
                self.runner,
                case.gold_sql,
                case.pred_sql,
                db_name=case.db_name,
                order_matters=case.order_matters,
                dialect=self.config.dialect,
            )
            result.ex = ex_match
            result.ex_detail = ex_detail
            result.gold_error = gold_outcome.error
            result.pred_error = pred_outcome.error

        if self.config.run_test_suite:
            ts_result = evaluate_test_suite(
                self.runner,
                case.db_name,
                case.case_id,
                case.gold_sql,
                case.pred_sql,
                config=self.config.test_suite,
                order_matters=case.order_matters,
                dialect=self.config.dialect,
            )
            result.test_suite_ex = ts_result.passed_all
            result.test_suite_pass_rate = ts_result.pass_rate
            result.test_suite_detail = {
                "gold_error": ts_result.gold_error,
                "instances": [
                    {"schema": i.schema_name, "match": i.match, "detail": i.detail}
                    for i in ts_result.instances
                ],
            }
            if ts_result.gold_error and not result.gold_error:
                result.gold_error = ts_result.gold_error

        return result

    def evaluate(self, cases: list[EvalCase]) -> EvalReport:
        return EvalReport(results=[self.evaluate_case(c) for c in cases])
