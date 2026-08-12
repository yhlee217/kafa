"""1.2 대변(상대계정) 결정.

[확정] 법인 클라이언트: 미지급비용(262), 거래처=카드사.
[보류] 개인사업자 클라이언트: 인출금 등 미확정 → config TODO. 법인 경로를 기본 동작으로.

주의(1.4): 다운로드본 '구분'(법인/일반)은 가맹점 법인격이지 고객사 아님.
상대계정은 '기장 클라이언트' 기준(client_type)으로 정한다.
"""
from __future__ import annotations

from kafa.config_loader import is_todo, load_rules
from kafa.rules.models import RuleResult, Verdict

RULE_CORPORATE = "CP-001"
RULE_INDIVIDUAL_FALLBACK = "CP-002"   # 개인: 대체 적용/미확정 → 확인 플래그
RULE_INDIVIDUAL = "CP-003"            # 개인 상대계정 확정(config)
RULE_UNRESOLVED = "CP-099"


def resolve_counterparty(
    client_type: str | None = None,
    *,
    config_dir: str | None = None,
) -> RuleResult:
    """상대계정 코드/거래처를 결정. value = (code, vendor) 튜플.

    client_type: 'corporate' | 'individual' | None(=config 기본값).
    """
    cfg = load_rules(config_dir)["counterparty"]
    if client_type is None:
        client_type = cfg.get("default_client_type", "corporate")

    corp = cfg["corporate_client"]
    corp_value = (int(corp["code"]), str(corp.get("vendor", "")))

    if client_type == "individual":
        ind = cfg.get("individual_client")
        if is_todo(ind) or not isinstance(ind, dict):
            # [보류] 개인 상대계정 미확정 → 법인 기본으로 폴백하되 검토 플래그.
            return RuleResult(
                corp_value, Verdict.REVIEW, RULE_INDIVIDUAL_FALLBACK,
                note="개인사업자 상대계정 미확정([보류]) → 법인 미지급비용으로 폴백. 담당자 확인.",
            )
        value = (int(ind["code"]), str(ind.get("vendor", "")))
        if ind.get("needs_review"):
            # 계정은 정해졌으나 대체 적용(예: 인출금 코드 미확보) → 확인 플래그 유지.
            return RuleResult(value, Verdict.REVIEW, RULE_INDIVIDUAL_FALLBACK,
                              note=str(ind.get("note") or "개인 상대계정 확인 필요"))
        return RuleResult(value, Verdict.RULE_CONFIRMED, RULE_INDIVIDUAL)

    # 법인(기본)
    return RuleResult(corp_value, Verdict.RULE_CONFIRMED, RULE_CORPORATE)
