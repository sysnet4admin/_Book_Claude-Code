"""Data models shared across the evaluator: input cases and output reports."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class EvalCase:
    """A single (gold SQL, predicted SQL) pair to evaluate.

    ``db_name`` selects which Postgres database the query runs against
    (schemas differ across text2sql benchmarks, e.g. Spider has one SQLite
    file per database id). ``order_matters`` should be True when the gold
    query has an ORDER BY that is part of the intended semantics; the
    evaluator also auto-detects this from the SQL when left as None.
    """

    case_id: str
    gold_sql: str
    pred_sql: str
    db_name: Optional[str] = None
    question: Optional[str] = None
    order_matters: Optional[bool] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Outcome of evaluating a single :class:`EvalCase`."""

    case_id: str
    em: Optional[bool] = None
    ex: Optional[bool] = None
    test_suite_ex: Optional[bool] = None
    test_suite_pass_rate: Optional[float] = None
    gold_error: Optional[str] = None
    pred_error: Optional[str] = None
    em_detail: Optional[str] = None
    ex_detail: Optional[str] = None
    test_suite_detail: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalReport:
    """Aggregate metrics + per-case results for a batch of :class:`EvalCase`."""

    results: list[EvalResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    def _accuracy(self, attr: str) -> Optional[float]:
        scored = [getattr(r, attr) for r in self.results if getattr(r, attr) is not None]
        if not scored:
            return None
        return sum(1 for v in scored if v) / len(scored)

    @property
    def em_accuracy(self) -> Optional[float]:
        return self._accuracy("em")

    @property
    def ex_accuracy(self) -> Optional[float]:
        return self._accuracy("ex")

    @property
    def test_suite_ex_accuracy(self) -> Optional[float]:
        return self._accuracy("test_suite_ex")

    def summary(self) -> dict[str, Any]:
        return {
            "total_cases": self.total,
            "exact_match_accuracy": self.em_accuracy,
            "execution_accuracy": self.ex_accuracy,
            "test_suite_execution_accuracy": self.test_suite_ex_accuracy,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }
