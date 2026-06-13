"""근거 생성 — 규칙ID + 맥락 → 사람이 읽는 한국어 근거 문장.

판정근거(DED-002 등 규칙ID)는 추적용 코드다. 담당자/리포트에는 이 모듈이 만든
자연어 근거를 함께 보여준다. 추천(LLM/시드) 행은 추천근거 문장을 그대로 쓴다.
"""
from __future__ import annotations

from kafa.rules.models import ClassifiedRow

# 규칙ID → 근거 문장(맥락 필요 시 람다)
_RULES = {
    "VAT-001": lambda r: f"위하고 유형 '{r.source.유형}'을 코드로 매핑",
    "VAT-002": lambda r: "과세/공제 상태로 유형 도출",
    "DED-001": lambda r: "면세사업 관련 등 자동 불공제",
    "DED-002": lambda r: f"국세청 컬럼('{r.source.국세청}')을 따라 공제여부 판정",
    "DED-003": lambda r: "업종/품명 키워드로 비영업용 승용차·접대성 의심 → 검토",
    "DED-004": lambda r: "국세청 값 미상 → 공제 기본",
    "ACC-001": lambda r: f"계정명 '{r.source.차변계정}' → 코드 매핑(정확)",
    "ACC-002": lambda r: f"계정명 '{r.source.차변계정}' → 코드 매핑(표기 정규화)",
    "ACC-099": lambda r: f"계정명 '{r.source.차변계정}' 미매핑 → 담당자 확인",
    "ACC-PENDING": lambda r: "미추천(차변계정 미정) → 계정 추천 대상",
    "CP-001": lambda r: "법인 상대계정 미지급비용(262)",
    "CP-002": lambda r: "개인 상대계정 미확정 → 법인(262) 폴백, 담당자 확인",
    "DEEM-001": lambda r: "카면(58)+고객사 음식점 → 의제 대상 후보",
    "DEEM-090": lambda r: "고객사 음식점 여부 미확정 → 의제 후보(검토)",
    "NEG-001": lambda r: "음수(환불/취소) → 차·대변 방향 반전",
    "SKIP-001": lambda r: f"전표상태 '{r.skip_reason}' → 스킵",
    "RECO-001": lambda r: None,   # 추천근거로 대체
}


def explain(row: ClassifiedRow) -> str:
    """행의 판정 근거를 한국어 문장으로. 추천행은 추천근거를 우선 노출."""
    parts: list[str] = []
    for rid in row.판정근거:
        fn = _RULES.get(rid)
        if fn is None:
            continue
        try:
            text = fn(row)
        except Exception:          # noqa: BLE001 — source 없음 등 방어
            text = None
        if text:
            parts.append(text)
    if row.추천근거:
        parts.append(row.추천근거)
    return " · ".join(parts)
