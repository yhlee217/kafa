"""리포트 공통 헬퍼.

여러 리포트(고객 요약·증빙 점검 등)가 거래를 '비-PII 식별자'로 표기한다 — 거래일자·설명
(품명|업태)·금액·마스킹 거래처. 중복을 막기 위해 한 곳에 둔다.
"""
from __future__ import annotations

from decimal import Decimal

from kafa.rules.models import ClassifiedRow
from kafa.security import mask_name


def row_identifier(r: ClassifiedRow, *, mask: bool = True) -> tuple[str, str, Decimal, str]:
    """(거래일자, 설명=품명|업태, 합계금액, 거래처) 반환. mask=True면 거래처 마스킹.

    고객/담당자가 PII 없이 날짜·금액·품목으로 거래를 식별하게 한다.
    """
    s = r.source
    if s is None:
        return "", "(품목 미상)", Decimal(0), ""
    date = f"{s.연도}-{s.일자}".strip("-")
    desc = (s.품명 or s.업태 or "").strip() or "(품목 미상)"
    amount = Decimal(s.합계 or 0)
    vendor = mask_name(s.거래처) if mask else (s.거래처 or "")
    return date, desc, amount, vendor
