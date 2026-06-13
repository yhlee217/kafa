"""1.3 불공제 3단 분기."""
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
