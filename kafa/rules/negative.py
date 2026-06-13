"""1.9 환불·승인취소(음수)·할부.

음수 건은 원거래와 동일 분류 유지, 차·대변 방향 반전.
할부는 승인 시점 1건 처리(별도 분할 없음).
"""
from __future__ import annotations

from decimal import Decimal

from kafa.rules.models import ClassifiedRow

RULE_REVERSAL = "NEG-001"


def is_negative(amount: Decimal) -> bool:
    return Decimal(amount) < 0


def apply_reversal(row: ClassifiedRow) -> ClassifiedRow:
    """차변/대변 코드를 맞바꿔 방향을 반전(분류 자체는 유지).

    reversal_applied 플래그로 중복 swap을 막는다(이미 적용됐으면 no-op).
    """
    if row.reversal_applied:
        return row
    row.차변계정코드, row.대변계정코드 = row.대변계정코드, row.차변계정코드
    row.is_reversal = True
    row.reversal_applied = True
    row.add_rule(RULE_REVERSAL)
    return row
