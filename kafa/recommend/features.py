"""미추천 행의 **비-PII 특징** 추출 — LLM 추정 입력용.

보안 제0원칙: 거래처 실명·사업자번호 등 PII 는 LLM 에 절대 보내지 않는다.
계정 추정은 가맹점 '업종(업태/종목)'과 '품명', '유형'만으로 충분하며, 금액은
계정 결정과 무관하므로 제외한다(특징 서명 안정화 → 결정 캐시 적중률↑, 재현성↑).
"""
from __future__ import annotations

import hashlib
import json
import re

from kafa.rules.models import InputRow

# LLM 에 전달 가능한 비-PII 필드만. (거래처/사업자등록번호/Code/관리 등은 제외)
ACCOUNT_FEATURE_FIELDS = ("업태", "종목", "품명", "유형")

# 절대 특징에 포함하면 안 되는 PII 필드(검증용).
FORBIDDEN_PII_FIELDS = ("거래처", "사업자등록번호")


# 품명 안의 긴 숫자열(카드·사업자·전화·주문번호)은 가린다 — 계정 판단에 쓸모가 없다.
_LONG_DIGITS = re.compile(r"\d[\d\- ]{4,}\d")
_CORP_MARKS = re.compile(r"\(주\)|\(유\)|\(사\)|㈜|주식회사|유한회사|\s+")
_SPACES = re.compile(r"\s+")


def _core_name(name: str) -> str:
    return _CORP_MARKS.sub("", name or "")


def scrub_item(품명: str, 거래처: str = "") -> str:
    """품명에서 식별정보를 걷어낸다(방어선 — 품명은 대개 공란이다).

    - 긴 숫자열 → `#`
    - 가맹점 이름이 품명에 그대로 들어 있으면 → `(가맹점)` (법인 표기·공백 무시 비교)
    """
    text = _SPACES.sub(" ", (품명 or "").strip())
    if not text:
        return ""
    text = _LONG_DIGITS.sub("#", text)
    core = _core_name(거래처)
    if len(core) < 2:
        return text

    def _is_vendor(tok: str) -> bool:
        t = _core_name(tok)
        return bool(t) and (core in t or (len(t) >= 3 and t in core))

    toks = text.split(" ")
    out = ["(가맹점)" if _is_vendor(t) else t for t in toks]
    text = " ".join(out)
    if core in _core_name(text):          # 띄어쓰기가 갈라 놓은 경우 — 통째로 가린다
        return "(가맹점)"
    return text


def account_features(row: InputRow) -> dict[str, str]:
    """계정 추정용 비-PII 특징 dict. PII 필드는 구조적으로 포함될 수 없다."""
    return make_features(업태=row.업태, 종목=row.종목, 품명=row.품명, 유형=row.유형,
                         거래처=row.거래처)


def make_features(*, 업태: str = "", 종목: str = "", 품명: str = "", 유형: str = "",
                  거래처: str = "") -> dict[str, str]:
    """필드값 → 비-PII 특징. 거래처는 품명을 가리는 데만 쓰고 결과에 넣지 않는다."""
    feats = {
        "업태": (업태 or "").strip(),
        "종목": (종목 or "").strip(),
        "품명": scrub_item(품명 or "", 거래처 or ""),
        "유형": (유형 or "").strip(),
    }
    # 방어적 검증: 혹시라도 PII 가 섞이면 즉시 실패(개발 중 회귀 방지).
    assert set(feats).isdisjoint(FORBIDDEN_PII_FIELDS), "PII가 특징에 포함됨"
    return feats


def feature_signature(features: dict[str, str]) -> str:
    """동일 특징 → 동일 서명. 결정 캐시 키(재현성·중복 호출 제거)."""
    blob = json.dumps(features, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]
