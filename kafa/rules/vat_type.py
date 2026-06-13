"""1.1 과세유형 라벨 ↔ 코드, 유형 도출.

[확정] 코드: 3 일반 / 57 카과 / 58 카면 / 59 카영 / 63 화물.
불공제 전용 코드는 없다 → 불공제는 유형=일반(3) + 국세청 컬럼으로 구분.
"""
from __future__ import annotations

from typing import Optional

from kafa.config_loader import load_rules
from kafa.rules.models import Deduct, RuleResult, Verdict

RULE_LABEL_MAP = "VAT-001"     # 라벨→코드 매핑
RULE_DERIVE = "VAT-002"        # 과세/면세 + 공제여부 → 유형 도출
RULE_UNRESOLVED = "VAT-099"


def label_to_code(label: str, *, config_dir: str | None = None) -> Optional[int]:
    """유형 라벨(한글)→코드. 미지정/미상이면 None."""
    if not label:
        return None
    codes = load_rules(config_dir)["vat_type_codes"]
    return codes.get(label.strip())


def code_to_label(code: int, *, config_dir: str | None = None) -> Optional[str]:
    codes = load_rules(config_dir)["vat_type_codes"]
    for label, c in codes.items():
        if c == code:
            return label
    return None


def map_prefilled(label: str, *, config_dir: str | None = None) -> RuleResult:
    """위하고가 이미 채운 유형 라벨을 코드로 매핑(rule_confirmed)."""
    code = label_to_code(label, config_dir=config_dir)
    if code is None:
        return RuleResult(None, Verdict.UNRESOLVED, RULE_UNRESOLVED,
                          note=f"미상 유형 라벨: {label!r}")
    return RuleResult(code, Verdict.RULE_CONFIRMED, RULE_LABEL_MAP)


def derive_vat_type(
    taxation: str,
    deduct: Deduct,
    *,
    config_dir: str | None = None,
) -> RuleResult:
    """과세구분('과세'/'면세') + 공제여부 → 유형코드 (1.1 로직).

    과세+공제→카과(57), 면세+공제→카면(58, [보류] 검증), 불공제→일반(3).
    검토(REVIEW)는 아직 불공제로 확정되지 않았으므로 공제 경로로 도출하되
    needs_review 플래그는 호출측(엔진)에서 별도로 단다.
    """
    logic = load_rules(config_dir)["vat_type_logic"]

    if deduct == Deduct.NON_DEDUCTIBLE:
        label = logic["불공제"]
    elif (taxation or "").strip() == "면세":
        label = logic["면세_공제"]
    else:  # 과세(기본) + 공제/검토
        label = logic["과세_공제"]

    code = label_to_code(label, config_dir=config_dir)
    if code is None:
        return RuleResult(None, Verdict.UNRESOLVED, RULE_UNRESOLVED,
                          note=f"도출 라벨 매핑 실패: {label!r}")
    return RuleResult(code, Verdict.RULE_CONFIRMED, RULE_DERIVE)
