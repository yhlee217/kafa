"""다운로드본(.xlsx) 읽기 → InputRow 목록.

요약행(카드사별 매입/일반/합계)은 데이터가 아니므로 제외한다.
금액은 Decimal 로 변환(부동소수 오차 방지). pandas/openpyxl 사용.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from kafa.io_wehago.schema import (
    PENDING_ACCOUNT_TOKENS,
    REQUIRED_INPUT,
    SUMMARY_TOKENS,
)
from kafa.rules.models import InputRow


class InputFormatError(ValueError):
    """위하고 다운로드본 형식이 아님(필수 컬럼 누락 등)."""


def _to_decimal(value) -> Decimal:
    """금액 셀 → Decimal. 빈 셀·비숫자·NaN/Inf 는 0.

    주의: 빈 셀이 float('nan') 으로 들어오면 Decimal('nan') 이 **예외 없이** 만들어져
    이후 모든 비교(`> 0`)가 InvalidOperation 으로 터진다. 유한값만 통과시킨다.
    """
    if value is None or value == "":
        return Decimal(0)
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


def _s(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


_COUNT_RE = re.compile(r"\d+\s*건")     # "… : 82건" 같은 집계 문구


def _is_summary_row(거래처: str, 연도: str, 일자: str) -> bool:
    """하단 집계행 판별.

    실제 파일은 요약 문구가 **일자 칸**에 들어오고 거래처는 비어 있다
    (예: "카드사별  매입 : 11건", "합계(카드사:2) : 82건"). 연도가 비어 있는 것을
    강한 조건으로 두어 정상 거래행을 잘못 버리지 않는다.
    """
    if 거래처 in SUMMARY_TOKENS:
        return True
    if not 연도 and not 거래처:
        text = f"{거래처} {일자}"
        if _COUNT_RE.search(text) or any(tok in text for tok in SUMMARY_TOKENS):
            return True
    return False


def _account_or_blank(value: str) -> str:
    """차변계정 칸의 상태 문구('미추천')는 계정명이 아니므로 공란으로 정규화."""
    return "" if value in PENDING_ACCOUNT_TOKENS else value


def read_download_xlsx(path: str | Path) -> list[InputRow]:
    import pandas as pd  # 지연 import: 룰 모듈은 pandas 불필요

    df = pd.read_excel(path, dtype=object)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_INPUT if c not in df.columns]
    if missing:
        raise InputFormatError(
            f"위하고 다운로드본 형식이 아님: 필수 컬럼 누락 {missing}. "
            f"(읽은 컬럼 일부: {list(df.columns)[:8]})"
        )

    def col(row, name):
        return row[name] if name in row else None

    rows: list[InputRow] = []
    for idx, r in df.iterrows():
        거래처 = _s(col(r, "거래처"))
        연도 = _s(col(r, "연도"))
        일자 = _s(col(r, "일자"))
        if _is_summary_row(거래처, 연도, 일자):
            continue
        # 완전 빈 행 스킵
        if not any([연도, 일자, 거래처, _s(col(r, "품명"))]):
            continue
        rows.append(InputRow(
            연도=연도,
            일자=일자,
            code=_s(col(r, "Code")),
            거래처=거래처,
            구분=_s(col(r, "구분")),
            품명=_s(col(r, "품명")),
            공급가액=_to_decimal(col(r, "공급가액")),
            세액=_to_decimal(col(r, "세액")),
            비과세=_to_decimal(col(r, "비과세")),
            합계=_to_decimal(col(r, "합계")),
            국세청=_s(col(r, "국세청")),
            업태=_s(col(r, "업태")),
            종목=_s(col(r, "종목")),
            유형=_s(col(r, "유형")),
            차변계정=_account_or_blank(_s(col(r, "차변계정"))),
            대변계정=_s(col(r, "대변계정")),
            관리=_s(col(r, "관리")),
            전표상태=_s(col(r, "전표상태")),
            사업자등록번호=_s(col(r, "사업자등록번호")),
            raw_index=int(idx),
        ))
    return rows
