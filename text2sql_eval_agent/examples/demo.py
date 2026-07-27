"""End-to-end demo of the evaluator against the sample "company" database.

Usage:
    createdb text2sql_eval_test          # once
    psql text2sql_eval_test -f examples/schema.sql
    python examples/demo.py [--host ...] [--port ...] [--user ...] [--password ...] [--dbname ...]

Walks through all six sample cases in eval_samples.jsonl and prints a
table of EM / EX / Test-Suite EX per case, then explains the two cases
where EX alone is misleading and Test-Suite EX catches it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from text2sql_eval.db import PostgresRunner
from text2sql_eval.evaluator import Evaluator, EvaluatorConfig
from text2sql_eval.models import EvalCase
from text2sql_eval.test_suite import TestSuiteConfig

EXAMPLES_DIR = Path(__file__).parent


def load_cases() -> list[EvalCase]:
    cases = []
    for line in (EXAMPLES_DIR / "eval_samples.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        cases.append(EvalCase(**{k: v for k, v in data.items() if k != "question"}, question=data.get("question")))
    return cases


def fmt(value: bool | None) -> str:
    if value is None:
        return "  -  "
    return " PASS" if value else " FAIL"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--dbname", default="text2sql_eval_test")
    args = parser.parse_args()

    runner = PostgresRunner(
        host=args.host, port=args.port, user=args.user, password=args.password, dbname=args.dbname
    )
    config = EvaluatorConfig(run_execution=True, run_test_suite=True, test_suite=TestSuiteConfig(num_instances=5))
    evaluator = Evaluator(runner=runner, config=config)

    cases = load_cases()
    report = evaluator.evaluate(cases)

    print(f"{'case_id':<28} {'EM':<6} {'EX':<6} {'Test-Suite EX':<14} question")
    print("-" * 100)
    for case, result in zip(cases, report.results):
        print(
            f"{case.case_id:<28} {fmt(result.em):<6} {fmt(result.ex):<6} "
            f"{fmt(result.test_suite_ex):<14} {case.question}"
        )

    print()
    print("Summary:", json.dumps(report.summary(), indent=2, ensure_ascii=False))

    print()
    print("Why EX alone is not enough:")
    print("  'wrong_column_masked_bug' orders by the primary key instead of salary.")
    print("  On the original data the highest id happens to also be the highest earner,")
    print("  so plain EX reports a match. Test-Suite EX reruns both queries against")
    print("  several perturbed copies of the data where that coincidence is broken,")
    print("  and correctly flags the predicted query as wrong.")

    runner.close()


if __name__ == "__main__":
    main()
