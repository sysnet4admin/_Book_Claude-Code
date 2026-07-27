# text2sql-eval-agent

Text2SQL 기반 데이터분석 에이전트의 출력 SQL을 정답(gold) SQL과 비교해 **EM(Exact Match)** 과 **EX(Execution Accuracy)** 를 동시에 산출하고, EX의 약점을 보완하는 **Test-Suite EX**까지 계산하는 평가 도구입니다.

## 배경 및 참고 자료

이 프로젝트는 아래 세 가지 자료의 방법론을 조합해서 설계했습니다.

- **Spider 2.0 Evaluation Workflow & Harness** — gold/predicted SQL 쌍을 실제 데이터베이스에 실행해 결과 집합을 비교하는 실행 기반 평가 워크플로 구조(케이스 단위 실행 → 결과 비교 → 집계)를 참고했습니다.
- **sqlglot 공식 문서** — SQL을 파싱해 AST로 다루고, dialect 변환·식별자 정규화(`normalize_identifiers`)·스코프 분석(`optimizer.scope.traverse_scope`) 등을 이용해 텍스트가 달라도 구조가 같은 SQL을 정규화된 형태로 비교하는 방법을 참고했습니다 (`src/text2sql_eval/exact_match.py`).
- **Test-Suite Execution Accuracy for Semantic Parsing (Zhong, Yu & Klein, EMNLP 2020, ACL Anthology)** — 단일 DB에서의 EX는 "우연히 결과가 같아서" 통과하는 false positive를 걸러내지 못한다는 문제를 지적하고, 여러 개의 변형(perturbed) DB 인스턴스에서 동시에 실행 결과가 일치해야 정답으로 인정하는 방법론을 제안합니다. 이 프로젝트의 `test_suite.py`가 이 방법론을 (경량화된 방식으로) 구현합니다.

## 평가 지표

| 지표 | 설명 | DB 필요 여부 |
|---|---|---|
| **EM** | sqlglot으로 gold/pred SQL을 AST로 파싱한 뒤, 식별자 대소문자·테이블 별칭·`AND`/`OR`/`IN` 연산자 순서·`GROUP BY` 순서·숫자 리터럴 표기(`2.0` vs `2`) 등을 정규화(canonicalize)해서 문자열이 완전히 같은지 비교 | 불필요 |
| **EX** | gold/pred SQL을 실제 Postgres에 실행해 결과 집합을 비교. 컬럼 순서가 달라도(`SELECT a, b` vs `SELECT b, a`) 일치하는 컬럼 치환(permutation)을 찾아 매칭하고, gold에 `ORDER BY`가 없으면 행 순서는 무시하되 중복 행(bag semantics)은 그대로 반영 | 필요 |
| **Test-Suite EX** | 원본 DB의 스키마를 기반으로 데이터가 변형된(perturbed) 복제 DB N개를 만들어 그 위에서도 EX를 재검증. 모든 인스턴스에서 결과가 일치해야 최종 통과로 인정 — 원본 데이터에서만 우연히 맞았던 쿼리를 걸러냄 | 필요 |

세 지표는 서로 다른 것을 잡아냅니다: EM은 "쿼리를 구조적으로 정확히 똑같이 썼는가", EX는 "결과가 (한 번은) 맞는가", Test-Suite EX는 "결과가 우연이 아니라 진짜로 맞는가"를 봅니다. `examples/demo.py` 실행 결과가 이 차이를 직접 보여줍니다.

## 설치

Postgres 인스턴스가 필요합니다 (EM만 쓸 경우는 불필요).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 빠른 시작 (데모)

```bash
createdb text2sql_eval_test               # 최초 1회
psql text2sql_eval_test -f examples/schema.sql
python examples/demo.py --user postgres --password <PASSWORD> --dbname text2sql_eval_test
```

`examples/schema.sql`은 `departments`/`employees` 두 테이블로 된 작은 샘플 DB를 만들고, `examples/eval_samples.jsonl`에는 6개의 gold/pred SQL 쌍이 들어 있습니다. 데모를 실행하면 다음과 같은 표가 출력됩니다.

```
case_id                      EM     EX     Test-Suite EX  question
----------------------------------------------------------------------------------------------------
exact_copy                    PASS   PASS   PASS          List all department names.
syntactic_paraphrase          FAIL   PASS   PASS          Names and salaries of employees in Engineering, cheapest first.
semantic_paraphrase_subquery  FAIL   PASS   PASS          Names of employees earning more than the average salary.
wrong_column_masked_bug       FAIL   PASS   FAIL          Highest-paid employee in Engineering.
boundary_operator_bug         FAIL   PASS   FAIL          Employees earning strictly more than 5000.
clearly_wrong                 FAIL   FAIL   FAIL          Employees hired before 2020.
```

`wrong_column_masked_bug` 케이스가 이 프로젝트의 핵심을 보여줍니다: 예측 SQL이 `salary` 대신 `id`로 정렬하는 버그가 있지만, 샘플 데이터에서는 "가장 높은 id"와 "가장 높은 salary"가 같은 직원이라 **EX는 우연히 통과(PASS)** 합니다. Test-Suite EX는 데이터가 변형된 복제 DB 여러 개에서 같은 쿼리를 재실행해 이 우연이 깨지는 것을 확인하고 정확히 **FAIL**로 판정합니다.

## 사용법

### CLI

```bash
text2sql-eval \
  --cases examples/eval_samples.jsonl \
  --host localhost --port 5432 --user postgres --password <PASSWORD> --dbname text2sql_eval_test \
  --test-suite --test-suite-instances 5 \
  --output report.json
```

`--cases`는 JSON Lines 파일로, 한 줄에 하나씩 아래 필드를 가진 평가 케이스를 담습니다.

```json
{"case_id": "q1", "db_name": "company", "gold_sql": "SELECT ...", "pred_sql": "SELECT ..."}
```

- `db_name`은 Postgres **스키마** 이름입니다. gold/pred SQL은 스키마를 직접 하드코딩하지 말고(`FROM company.employees`가 아니라 `FROM employees`) 스키마 이름 없이 작성해야 `db_name`으로 지정한 스키마를 대상으로 정확히 실행됩니다.
- `--no-execution`을 주면 DB 연결 없이 EM만 계산합니다.
- `--test-suite`를 주지 않으면 EX만 계산합니다 (Test-Suite EX는 DB 복제 비용이 있으므로 기본은 off).

### 라이브러리로 사용하기

```python
from text2sql_eval.db import PostgresRunner
from text2sql_eval.evaluator import Evaluator, EvaluatorConfig
from text2sql_eval.models import EvalCase
from text2sql_eval.test_suite import TestSuiteConfig

runner = PostgresRunner(host="localhost", user="postgres", password="...", dbname="text2sql_eval_test")
evaluator = Evaluator(
    runner=runner,
    config=EvaluatorConfig(run_execution=True, run_test_suite=True, test_suite=TestSuiteConfig(num_instances=5)),
)

report = evaluator.evaluate([
    EvalCase(case_id="q1", db_name="company", gold_sql="SELECT ...", pred_sql="SELECT ..."),
])

print(report.summary())
# {'total_cases': 1, 'exact_match_accuracy': ..., 'execution_accuracy': ..., 'test_suite_execution_accuracy': ...}
```

EM만 필요하면 DB 없이도 바로 쓸 수 있습니다.

```python
from text2sql_eval.exact_match import is_exact_match

result = is_exact_match("SELECT a, b FROM t WHERE x = 1", "select b, a from t where x = 1")
result.match  # True
```

## 테스트 실행

```bash
export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=<PASSWORD> PGDATABASE=text2sql_eval_test
pytest
```

Postgres 인스턴스에 연결할 수 없으면 EX/Test-Suite EX 관련 테스트는 자동으로 skip되고 EM 테스트만 실행됩니다.

## 파일 구조

```
text2sql_eval_agent/
├── src/text2sql_eval/
│   ├── models.py        # EvalCase, EvalResult, EvalReport
│   ├── db.py             # Postgres 실행 레이어 (psycopg)
│   ├── exact_match.py    # EM: sqlglot AST 정규화 비교
│   ├── execution.py      # EX: 결과 집합 비교 (컬럼 순서 무관, bag semantics)
│   ├── test_suite.py     # Test-Suite EX: 데이터 변형 + 다중 인스턴스 검증
│   ├── evaluator.py       # EM/EX/Test-Suite EX 통합 오케스트레이터
│   └── cli.py             # `text2sql-eval` CLI
├── examples/
│   ├── schema.sql         # 샘플 DB (departments, employees)
│   ├── eval_samples.jsonl # 6개 gold/pred SQL 케이스
│   └── demo.py            # 엔드투엔드 데모 스크립트
└── tests/
```

## 알려진 제한사항

- EM의 테이블 별칭 정규화는 스코프 단위(서브쿼리별)로 동작하지만, 두 개의 `INNER JOIN` 순서를 바꾸는 것처럼 FROM/JOIN 절 자체의 순서를 정규화하지는 않습니다.
- 컬럼에 스키마 정보 없이 테이블 한정자(`t.col`)가 있는지 없는지까지는 정규화하지 않습니다 — 스키마를 넘겨 완전한 컬럼 qualify를 수행하는 것은 향후 개선 과제입니다.
- Test-Suite EX의 데이터 변형(값 셔플, NULL 주입, 중복 행, 숫자 jitter)은 Postgres의 `random()`을 사용하므로 변형 자체를 시드로 완전히 재현할 수는 없습니다 (어떤 변형을 *적용할지* 결정하는 로직은 시드로 고정되지만, *어떤 행이* 뽑히는지는 매 실행마다 달라질 수 있습니다).
- 현재는 Postgres 전용입니다. 다른 DB 엔진을 지원하려면 `db.py`의 `PostgresRunner`를 교체하면 됩니다.
