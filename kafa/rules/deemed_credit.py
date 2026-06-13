"""1.6 의제매입세액 — 플래그만. 율/한도 계산 금지.

카면(58) + 고객사 음식점업 → '의제 대상 후보' 플래그 + 면세매입액만 산출.
공제율(개인 8/108 등)·한도는 사업자/기간 변수이고 2026.1.1 한도율 축소 → 전표 단계에서
계산하지 않고 부가세 신고 단계로 넘긴다.

[보류] 고객사 음식점 여부(client_is_restaurant)는 별도 회사정보 필요. config TODO.
가맹점 업태/종목과는 다른 정보(1.5).
"""
from __future__ import annotations

from decimal import Decimal

from kafa.config_loader import is_todo, load_rules
from kafa.rules.models import RuleResult, Verdict

RULE_FLAG = "DEEM-001"
RULE_NOT_APPLICABLE = "DEEM-000"
RULE_PENDING = "DEEM-090"   # 고객사 음식점 여부 미확정

CODE_카면 = 58


def deemed_credit_flag(
    유형코드: int | None,
    면세금액: Decimal,
    client_is_restaurant: bool | None = None,
    *,
    config_dir: str | None = None,
) -> RuleResult:
    """value = (의제대상여부: bool, 면세매입액: Decimal). 율/세액 계산은 하지 않는다."""
    cfg = load_rules(config_dir)["restaurant_deemed_credit"]

    if 유형코드 != CODE_카면:
        return RuleResult((False, Decimal(0)), Verdict.RULE_CONFIRMED, RULE_NOT_APPLICABLE)

    # 고객사 음식점 여부: 인자 우선, 없으면 config.
    if client_is_restaurant is None:
        raw = cfg.get("client_is_restaurant")
        if is_todo(raw) or raw is None:
            # [보류] 회사정보 미확보 → 후보로 두되 검토 플래그.
            return RuleResult((True, Decimal(면세금액)), Verdict.REVIEW, RULE_PENDING,
                              note="고객사 음식점 여부 미확정([보류]) → 의제 후보로 표기, 신고단계 확인.")
        client_is_restaurant = bool(raw)

    if client_is_restaurant:
        return RuleResult((True, Decimal(면세금액)), Verdict.RULE_CONFIRMED, RULE_FLAG,
                          note="의제 대상 후보. 율/한도는 부가세 신고 단계.")
    return RuleResult((False, Decimal(0)), Verdict.RULE_CONFIRMED, RULE_NOT_APPLICABLE)
