"""미추천 행의 **비-PII 특징** 추출 — LLM 추정 입력용.

보안 제0원칙: 거래처 실명·사업자번호 등 PII 는 LLM 에 절대 보내지 않는다.
계정 추정은 가맹점 '업종(업태/종목)'과 '품명', '유형'만으로 충분하며, 금액은
계정 결정과 무관하므로 제외한다(특징 서명 안정화 → 결정 캐시 적중률↑, 재현성↑).
"""
from __future__ import annotations

import hashlib
import json

from kafa.rules.models import InputRow

# LLM 에 전달 가능한 비-PII 필드만. (거래처/사업자등록번호/Code/관리 등은 제외)
ACCOUNT_FEATURE_FIELDS = ("업태", "종목", "품명", "유형")

# 절대 특징에 포함하면 안 되는 PII 필드(검증용).
FORBIDDEN_PII_FIELDS = ("거래처", "사업자등록번호")


def account_features(row: InputRow) -> dict[str, str]:
    """계정 추정용 비-PII 특징 dict. PII 필드는 구조적으로 포함될 수 없다."""
    feats = {
        "업태": (row.업태 or "").strip(),
        "종목": (row.종목 or "").strip(),
        "품명": (row.품명 or "").strip(),
        "유형": (row.유형 or "").strip(),
    }
    # 방어적 검증: 혹시라도 PII 가 섞이면 즉시 실패(개발 중 회귀 방지).
    assert set(feats).isdisjoint(FORBIDDEN_PII_FIELDS), "PII가 특징에 포함됨"
    return feats


def feature_signature(features: dict[str, str]) -> str:
    """동일 특징 → 동일 서명. 결정 캐시 키(재현성·중복 호출 제거)."""
    blob = json.dumps(features, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]
