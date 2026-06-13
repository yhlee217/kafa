"""엔진 통합 — 합성(가짜) 입력만 사용. 실제 PII 없음."""
from decimal import Decimal

from kafa.rules.engine import classify_row
from kafa.rules.models import Deduct, InputRow, Verdict


def _row(**kw) -> InputRow:
    base = dict(연도="2026", 일자="03-15", 거래처="합성가맹점",
                공급가액=Decimal("10000"), 세액=Decimal("1000"),
                합계=Decimal("11000"), 국세청="공제", 유형="카과",
                차변계정="(판)복리후생비", 전표상태="", 사업자등록번호="000-00-00000")
    base.update(kw)
    return InputRow(**base)


def test_prefilled_row_maps_only():
    c = classify_row(_row(), client_type="corporate")
    assert c.유형코드 == 57
    assert c.차변계정코드 == 811
    assert c.대변계정코드 == 262
    assert c.대변거래처 == "카드사"
    assert c.공제여부 == Deduct.DEDUCTIBLE
    assert c.판정유형 == Verdict.RULE_CONFIRMED
    assert not c.needs_review


def test_unrecommended_row_is_unresolved():
    c = classify_row(_row(전표상태="미추천", 차변계정=""), client_type="corporate")
    assert c.차변계정코드 is None
    assert c.판정유형 == Verdict.UNRESOLVED
    assert "ACC-PENDING" in c.판정근거


def test_duplicate_skipped():
    c = classify_row(_row(전표상태="중복전표"))
    assert c.skipped is True
    assert c.skip_reason == "중복전표"


def test_non_deductible_overrides_type_to_ilban():
    c = classify_row(_row(국세청="불공제", 업태="도매"), client_type="corporate")
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE
    assert c.유형코드 == 3   # 카과(57) → 일반(3) 오버라이드


def test_fuel_flags_review_keeps_prefilled_type():
    c = classify_row(_row(업태="주유소", 종목="경유"), client_type="corporate")
    assert c.공제여부 == Deduct.REVIEW
    assert c.needs_review is True
    assert c.유형코드 == 57   # 자동 확정 금지 → 위하고 유형 유지


def test_negative_reverses_direction():
    c = classify_row(_row(차변계정="(판)차량유지비", 합계=Decimal("-11000")),
                     client_type="corporate")
    assert c.is_reversal is True
    assert c.차변계정코드 == 262   # swap 적용
    assert c.대변계정코드 == 822


def test_individual_client_falls_back_to_corporate_with_review():
    c = classify_row(_row(), client_type="individual")
    # 개인 상대계정 [보류] → 법인(262)로 폴백 + 검토 플래그
    assert c.대변계정코드 == 262
    assert c.needs_review is True
