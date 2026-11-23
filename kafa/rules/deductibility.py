"""1.3 불공제 3단 분기.

기본값은 config `non_deductible.source` 가 정한다.
  유형        : 담당자가 위하고에서 정하는 유형(카과 → 공제, 일반 → 불공제). 표에 없는
                유형(카면·카영·공란)은 국세청 컬럼으로 폴백. 근거는 docs/domain_notes.md.
  국세청_컬럼 : 국세청 컬럼을 따른다(예전 방식).
  자동 불공제 : 면세사업관련 등 (config non_deductible.auto).
  검토 플래그 : 비영업용 승용차/접대성 업종 등 (자동 확정 금지 → Deduct.REVIEW).
  나머지      : 공제.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import lru_cache

from kafa.config_loader import load_account_codes, load_rules
from kafa.rules.models import Deduct, RuleResult, Verdict

RULE_AUTO_NON = "DED-001"      # 룰 확정 자동 불공제
RULE_FROM_NTS = "DED-002"      # 국세청 컬럼을 따름
RULE_REVIEW = "DED-003"        # 의심 → 담당자 확인 플래그
RULE_DEFAULT_DEDUCT = "DED-004"
RULE_SERVICE_CHARGE = "DED-005"  # 봉사료(접대성) → 불공제 (담당자 확인 2026-08)
RULE_FROM_TYPE = "DED-006"     # 담당자가 정한 유형을 따름 (담당자 확인 2026-11)
RULE_BY_ACCOUNT = "DED-007"    # 계정이 공제 여부를 정함 (접대비 → 불공제, 부가세법)

_NTS_NON = {"불공제", "불공", "0"}
_NTS_DEDUCT = {"공제", "1"}


def _haystack(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _positive(amount) -> bool:
    """금액이 0보다 큰가(Decimal/숫자/문자 모두 허용, 파싱 실패는 False)."""
    try:
        return Decimal(str(amount or 0)) > 0
    except (InvalidOperation, ValueError):
        return False


def resolve_deductibility(
    nts_value: str,
    업태: str = "",
    종목: str = "",
    품명: str = "",
    *,
    봉사료=0,
    유형: str = "",
    config_dir: str | None = None,
) -> RuleResult:
    """공제여부 판정. 검토 대상이면 Deduct.REVIEW(자동 확정 금지).

    봉사료: 다운로드본의 비과세(=봉사료 추정) 금액. 담당자 확인(2026-08) — 봉사료가
    붙는 거래는 접대성 경비로 보아 **불공제**. 다만 '비과세=봉사료' 동일성이 아직
    확인되지 않아(config confirmed_identity=false) 불공제로 처리하되 검토 플래그를 남긴다.
    """
    cfg = load_rules(config_dir)["non_deductible"]
    text = _haystack(업태, 종목, 품명)

    # 0) 봉사료 발생 → 접대성 경비로 불공제(담당자 확인). 국세청 값보다 우선.
    sc = cfg.get("service_charge", {}) or {}
    if sc.get("non_deductible") and _positive(봉사료):
        if sc.get("confirmed_identity"):
            return RuleResult(Deduct.NON_DEDUCTIBLE, Verdict.RULE_CONFIRMED,
                              RULE_SERVICE_CHARGE, note="봉사료(접대성) → 불공제")
        return RuleResult(
            Deduct.NON_DEDUCTIBLE, Verdict.REVIEW, RULE_SERVICE_CHARGE,
            note="봉사료(접대성) → 불공제. 비과세=봉사료 동일성 미확인 → 담당자 확인.")

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

    # 3) 기본값: 담당자가 정한 유형(카과/일반). 표에 없는 유형이면 국세청 컬럼으로 폴백.
    if cfg.get("source") == "유형":
        label = (유형 or "").strip()
        decided = str((cfg.get("by_type") or {}).get(label, "")).strip()
        if decided in _NTS_NON:
            return RuleResult(Deduct.NON_DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_FROM_TYPE,
                              note=f"유형 {label} → 불공제")
        if decided in _NTS_DEDUCT:
            return RuleResult(Deduct.DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_FROM_TYPE,
                              note=f"유형 {label} → 공제")

    # 3') 국세청 컬럼.
    v = (nts_value or "").strip()
    if v in _NTS_NON:
        return RuleResult(Deduct.NON_DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_FROM_NTS)
    if v in _NTS_DEDUCT:
        return RuleResult(Deduct.DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_FROM_NTS)

    # 국세청 값이 비거나 미상 → 공제 기본(보수적으로 근거 표시).
    return RuleResult(Deduct.DEDUCTIBLE, Verdict.RULE_CONFIRMED, RULE_DEFAULT_DEDUCT,
                      note=f"국세청 값 미상: {nts_value!r} → 공제 기본")


@lru_cache(maxsize=None)
def _account_decisions(config_dir: str | None) -> dict[int, str]:
    """계정코드 → '공제'/'불공제'. config `non_deductible.by_account` 의 말이 계정명에 들어 있으면."""
    rules = (load_rules(config_dir).get("non_deductible") or {}).get("by_account") or {}
    out: dict[int, str] = {}
    for name, code in load_account_codes(config_dir).items():
        for word, decision in rules.items():
            if word and str(word) in str(name):
                out[int(code)] = str(decision).strip()
    return out


def apply_account_rule(c, *, config_dir: str | None = None) -> bool:
    """비용 계정이 공제 여부를 정하는 경우(접대비 → 불공제) 공제여부·유형을 맞춘다.

    계정이 정해진 뒤에 부른다(위하고가 채운 계정은 엔진에서, 추천 계정은 추천 직후).
    환불로 차·대변이 이미 뒤바뀐 행은 대변 쪽이 비용 계정이다. 바꿨으면 True.
    """
    code = c.대변계정코드 if c.reversal_applied else c.차변계정코드
    if code is None:
        return False
    decided = _account_decisions(config_dir).get(int(code))
    if decided in _NTS_NON:
        target = Deduct.NON_DEDUCTIBLE
    elif decided in _NTS_DEDUCT:
        target = Deduct.DEDUCTIBLE
    else:
        return False
    vat_codes = load_rules(config_dir).get("vat_type_codes") or {}
    ilban = vat_codes.get("일반")
    if target == Deduct.NON_DEDUCTIBLE:
        if c.공제여부 == target and c.유형코드 == ilban:
            return False
        c.공제여부 = target
        c.유형코드 = ilban                 # 불공제 → 유형 일반(엔진의 불공제 오버라이드와 같은 규칙)
        c.의제대상여부 = False              # 접대 경비는 의제매입(원재료) 대상이 아니다
        c.면세매입액 = Decimal(0)
    else:
        if c.공제여부 == target:
            return False
        c.공제여부 = target
    c.add_rule(RULE_BY_ACCOUNT)
    return True
