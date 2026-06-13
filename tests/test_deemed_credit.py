"""1.6 의제매입 — 플래그만(율/세액 계산 안 함)."""
from decimal import Decimal

from kafa.rules.deemed_credit import deemed_credit_flag
from kafa.rules.models import Verdict


def test_not_kamyeon_no_flag():
    의제, 면세 = deemed_credit_flag(57, Decimal("10000")).value
    assert 의제 is False
    assert 면세 == Decimal(0)


def test_kamyeon_restaurant_flag():
    r = deemed_credit_flag(58, Decimal("10000"), client_is_restaurant=True)
    의제, 면세 = r.value
    assert 의제 is True
    assert 면세 == Decimal("10000")
    assert r.verdict == Verdict.RULE_CONFIRMED


def test_kamyeon_not_restaurant():
    r = deemed_credit_flag(58, Decimal("10000"), client_is_restaurant=False)
    의제, 면세 = r.value
    assert 의제 is False


def test_kamyeon_pending_config_todo_is_review():
    # config client_is_restaurant=TODO → 후보로 두되 검토 플래그
    r = deemed_credit_flag(58, Decimal("10000"))
    의제, 면세 = r.value
    assert 의제 is True
    assert r.verdict == Verdict.REVIEW
    assert 면세 == Decimal("10000")
