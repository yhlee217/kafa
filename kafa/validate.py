"""순수 검증 함수 — 사업자등록번호 체크섬 / 부가율(VAT) 이상.

Phase 4 검토 리포트의 '홈택스 미등록 의심'·'부가율 이상' 판정에 쓰인다.
홈택스 API 없이 로컬에서 근사 판정한다(체크섬 무효/부가율 편차).
"""
from __future__ import annotations

import re
from decimal import Decimal

_DIGITS = re.compile(r"\D")

# 사업자등록번호 체크섬 가중치 (국세청 알고리즘)
_BIZNO_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)

STANDARD_VAT_RATE = Decimal("0.10")   # 표준 부가가치세율 10%


def valid_bizno(bizno: str) -> bool:
    """사업자등록번호 10자리 체크섬 검증.

    합 = Σ d[i]*w[i] (i=0..8) + (d[8]*5)//10
    검증숫자 = (10 - (합 % 10)) % 10  == d[9]
    """
    digits = _DIGITS.sub("", bizno or "")
    if len(digits) != 10 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    total = sum(d[i] * _BIZNO_WEIGHTS[i] for i in range(9))
    total += (d[8] * 5) // 10
    check = (10 - (total % 10)) % 10
    return check == d[9]


def effective_vat_rate(공급가액, 세액) -> Decimal | None:
    """세액/공급가액. 공급가액이 0 이하면 None(산정 불가)."""
    supply = Decimal(공급가액 or 0)
    tax = Decimal(세액 or 0)
    if supply <= 0:
        return None
    return tax / supply


def vat_rate_anomaly(공급가액, 세액, *, tol: float = 0.01) -> str | None:
    """신용카드 매입 부가율 이상 사유를 반환(정상이면 None).

    - 공급가액>0, 세액>0: 표준 10%에서 tol(절대 편차) 초과 → 이상.
    - 공급가액<=0, 세액>0: 공급가액 없는데 세액 존재 → 이상.
    - 세액==0: 면세/비과세 가능 → 이상 아님.
    """
    supply = Decimal(공급가액 or 0)
    tax = Decimal(세액 or 0)
    if tax <= 0:
        return None
    if supply <= 0:
        return "공급가액 0인데 세액 존재"
    rate = tax / supply
    if abs(rate - STANDARD_VAT_RATE) > Decimal(str(tol)):
        return f"부가율 {rate:.1%}(표준 10% 대비 편차)"
    return None
