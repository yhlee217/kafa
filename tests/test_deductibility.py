"""1.3 불공제 3단 분기."""
from decimal import Decimal

from kafa.rules.deductibility import resolve_deductibility
from kafa.rules.models import Deduct, Verdict


def test_default_follows_nts_deductible():
    r = resolve_deductibility("공제", 업태="도매", 종목="문구")
    assert r.value == Deduct.DEDUCTIBLE
    assert r.rule_id == "DED-002"


def test_default_follows_nts_non_deductible():
    r = resolve_deductibility("불공제", 업태="도매", 종목="문구")
    assert r.value == Deduct.NON_DEDUCTIBLE
    assert r.rule_id == "DED-002"


def test_review_flag_for_fuel_even_if_nts_deductible():
    # 국세청=공제여도 비영업용 승용차 의심(주유) → 검토(자동 확정 금지)
    r = resolve_deductibility("공제", 업태="주유소", 종목="휘발유")
    assert r.value == Deduct.REVIEW
    assert r.verdict == Verdict.REVIEW
    assert r.rule_id == "DED-003"


def test_review_flag_by_pummyeong_keyword():
    r = resolve_deductibility("공제", 품명="LPG 충전")
    assert r.value == Deduct.REVIEW


def test_entertainment_review():
    r = resolve_deductibility("공제", 업태="유흥주점")
    assert r.value == Deduct.REVIEW


def test_unknown_nts_defaults_deductible():
    r = resolve_deductibility("", 업태="도매")
    assert r.value == Deduct.DEDUCTIBLE
    assert r.rule_id == "DED-004"


# ── 봉사료(비과세) → 불공제 (담당자 확인 2026-08) ──

def test_service_charge_is_non_deductible_with_review():
    # 봉사료가 붙으면 접대성 경비로 불공제. 단 비과세=봉사료 동일성 미확인 → 검토 플래그.
    r = resolve_deductibility("공제", 업태="음식점", 봉사료=Decimal("500"))
    assert r.value == Deduct.NON_DEDUCTIBLE
    assert r.verdict == Verdict.REVIEW
    assert r.rule_id == "DED-005"


def test_service_charge_overrides_nts_deductible():
    r = resolve_deductibility("공제", 봉사료=1000)
    assert r.value == Deduct.NON_DEDUCTIBLE and r.rule_id == "DED-005"


def test_no_service_charge_keeps_normal_path():
    r = resolve_deductibility("공제", 업태="도매", 봉사료=0)
    assert r.value == Deduct.DEDUCTIBLE and r.rule_id == "DED-002"


def test_service_charge_bad_value_ignored():
    r = resolve_deductibility("공제", 업태="도매", 봉사료="")
    assert r.value == Deduct.DEDUCTIBLE
