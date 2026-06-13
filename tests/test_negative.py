"""1.9 환불/취소(음수) 방향 반전."""
from decimal import Decimal

from kafa.rules.models import ClassifiedRow
from kafa.rules.negative import apply_reversal, is_negative


def test_is_negative():
    assert is_negative(Decimal("-1000"))
    assert not is_negative(Decimal("0"))
    assert not is_negative(Decimal("1000"))


def test_apply_reversal_swaps_codes():
    row = ClassifiedRow(차변계정코드=822, 대변계정코드=262)
    apply_reversal(row)
    assert row.차변계정코드 == 262
    assert row.대변계정코드 == 822
    assert row.is_reversal is True
    assert row.reversal_applied is True
    assert "NEG-001" in row.판정근거


def test_apply_reversal_idempotent():
    row = ClassifiedRow(차변계정코드=822, 대변계정코드=262)
    apply_reversal(row)
    apply_reversal(row)  # 두 번 호출해도 한 번만 swap
    assert row.차변계정코드 == 262
    assert row.대변계정코드 == 822
