"""1.3 불공제 3단 분기.

국세청 컬럼은 위하고 자동추정값(참고). 담당자가 수동 오버라이드하는 사례 있음.
  기본값      : 국세청 컬럼을 따른다.
  자동 불공제 : 면세사업관련 등 (config non_deductible.auto).
  검토 플래그 : 비영업용 승용차/접대성 업종 등 (자동 확정 금지 → Deduct.REVIEW).
  나머지      : 공제.
"""
from __future__ import annotations

from kafa.config_loader import load_rules
from kafa.rules.models import Deduct, RuleResult, Verdict

RULE_AUTO_NON = "DED-001"      # 룰 확정 자동 불공제
RULE_FROM_NTS = "DED-002"      # 국세청 컬럼을 따름
RULE_REVIEW = "DED-003"        # 의심 → 담당자 확인 플래그
RULE_DEFAULT_DEDUCT = "DED-004"

_NTS_NON = {"불공제", "불공", "0"}
_NTS_DEDUCT = {"공제", "1"}


def _haystack(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def resolve_deductibility(
    nts_value: str,
    업태: str = "",
    종목: str = "",
    품명: str = "",
    *,
    config_dir: str | None = None,
) -> RuleResult:
    """공제여부 판정. 검토 대상이면 Deduct.REVIEW(자동 확정 금지)."""
    cfg = load_rules(config_dir)["non_deductible"]
    text = _haystack(업태, 종목, 품명)

    # 1) 검토 키워드(의심) 먼저 — 같은 가맹점도 목적에 따라 갈리므로 자동 확정 금지.
    review_kw = cfg.get("review_keywords", {}) or {}
    for category in cfg.get("flag_for_review", []) or []:
        keywords = review_kw.get(category, []) or []
        if any(kw and kw in text for kw in keywords):
            return RuleResult(
                Deduct.REVIEW, Verdict.REVIEW, RULE_REVIEW,
                note=f"의심 카테고리: {category}",
            )

    # 2) 자동 불공제(룰 확정) — auto 카테고리 키워드/업종 매칭.
    #    현재 'auto' 는 카테고리명만 주어짐. 키워드 사전 보강 시 확장.
    auto_kw = cfg.get("auto_keywords", {}) or {}
    for category in cfg.get("auto", []) or []:
        keywords = auto_kw.get(category, []) or []
        if keywords and any(kw and kw in text for kw in keywords):
            return RuleResult(
                Deduct.NON_DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_AUTO_NON,
                note=f"자동 불공제: {category}",
            )

    # 3) 기본값: 국세청 컬럼.
    v = (nts_value or "").strip()
    if v in _NTS_NON:
        return RuleResult(Deduct.NON_DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_FROM_NTS)
    if v in _NTS_DEDUCT:
        return RuleResult(Deduct.DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_FROM_NTS)

    # 국세청 값이 비거나 미상 → 공제 기본(보수적으로 근거 표시).
    return RuleResult(Deduct.DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_DEFAULT_DEDUCT,
                      note=f"국세청 값 미상: {nts_value!r} → 공제 기본")
