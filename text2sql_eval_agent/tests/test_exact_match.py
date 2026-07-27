import pytest

from text2sql_eval.exact_match import is_exact_match


@pytest.mark.parametrize(
    "gold, pred, expected",
    [
        ("SELECT a FROM t", "SELECT a FROM t", True),
        ("SELECT a FROM t", "SELECT A FROM T", True),
        ("SELECT a, b FROM t WHERE x = 1 AND y = 2", "select b, a from t where y = 2 and x = 1", True),
        (
            "SELECT t1.name FROM users AS t1 JOIN orders AS t2 ON t1.id = t2.user_id WHERE t2.amount > 10",
            "SELECT u.name FROM users AS u JOIN orders AS o ON u.id = o.user_id WHERE o.amount > 10",
            True,
        ),
        ("SELECT name FROM t WHERE x IN (1,2,3)", "SELECT name FROM t WHERE x IN (3,2,1)", True),
        ("SELECT name FROM t GROUP BY a, b", "SELECT name FROM t GROUP BY b, a", True),
        ("SELECT name FROM t WHERE x = 2.0", "SELECT name FROM t WHERE x = 2", True),
        ("SELECT name FROM t ORDER BY a, b", "SELECT name FROM t ORDER BY b, a", False),
        ("SELECT name FROM t WHERE x > 5", "SELECT name FROM t WHERE x > 6", False),
        ("SELECT name FROM t WHERE x = 1", "SELECT name FROM t WHERE x = 1 AND 1 = 1", False),
    ],
)
def test_is_exact_match(gold, pred, expected):
    result = is_exact_match(gold, pred)
    assert result.match == expected, (result.gold_canonical, result.pred_canonical)


def test_select_order_can_be_made_order_sensitive():
    gold = "SELECT a, b FROM t"
    pred = "SELECT b, a FROM t"
    assert is_exact_match(gold, pred, ignore_select_order=True).match is True
    assert is_exact_match(gold, pred, ignore_select_order=False).match is False


def test_unparsable_sql_is_not_a_match_and_reports_error():
    result = is_exact_match("SELECT a FROM t", "SELECT FROM WHERE @@@")
    assert result.match is False
    assert result.error is not None
