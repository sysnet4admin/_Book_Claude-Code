"""Command-line entrypoint: evaluate a JSONL dataset of (gold, pred) SQL pairs.

Example:
    text2sql-eval --cases examples/eval_samples.jsonl \\
        --host localhost --port 5432 --user postgres --password postgres --dbname text2sql_eval_test \\
        --test-suite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from text2sql_eval.db import PostgresRunner
from text2sql_eval.evaluator import Evaluator, EvaluatorConfig
from text2sql_eval.models import EvalCase
from text2sql_eval.test_suite import TestSuiteConfig


def load_cases(path: Path) -> list[EvalCase]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            cases.append(
                EvalCase(
                    case_id=str(data.get("case_id") or data.get("id")),
                    gold_sql=data["gold_sql"],
                    pred_sql=data["pred_sql"],
                    db_name=data.get("db_name"),
                    question=data.get("question"),
                    order_matters=data.get("order_matters"),
                )
            )
    return cases


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a text2sql agent with EM + EX (+ Test-Suite EX).")
    parser.add_argument("--cases", type=Path, required=True, help="JSONL file of gold/pred SQL pairs")
    parser.add_argument("--dialect", default="postgres")
    parser.add_argument("--no-execution", action="store_true", help="skip EX (EM only, no DB needed)")
    parser.add_argument("--test-suite", action="store_true", help="also compute Test-Suite EX")
    parser.add_argument("--test-suite-instances", type=int, default=5)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="")
    parser.add_argument("--dbname", default="postgres")
    parser.add_argument("--output", type=Path, help="write full JSON report here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cases = load_cases(args.cases)
    if not cases:
        print(f"No cases found in {args.cases}", file=sys.stderr)
        return 1

    run_execution = not args.no_execution
    config = EvaluatorConfig(
        dialect=args.dialect,
        run_execution=run_execution,
        run_test_suite=args.test_suite,
        test_suite=TestSuiteConfig(num_instances=args.test_suite_instances),
    )

    runner = None
    if run_execution or args.test_suite:
        runner = PostgresRunner(
            host=args.host, port=args.port, user=args.user, password=args.password, dbname=args.dbname
        )

    evaluator = Evaluator(runner=runner, config=config)
    try:
        report = evaluator.evaluate(cases)
    finally:
        if runner is not None:
            runner.close()

    summary = report.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.output:
        args.output.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report written to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
