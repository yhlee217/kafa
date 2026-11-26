"""엔진 통합 — 합성(가짜) 입력만 사용. 실제 PII 없음."""
from decimal import Decimal
from pathlib import Path

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


def test_type_decides_deduction_over_the_nts_column():
    """[확정 2026-11-23 담당자] 공제 여부는 국세청 칸이 아니라 담당자가 정한 유형이 가른다.
    국세청이 불공제라도 유형이 카과면 공제이고, 유형도 그대로 둔다."""
    c = classify_row(_row(국세청="불공제", 업태="도매"), client_type="corporate")
    assert c.공제여부 == Deduct.DEDUCTIBLE
    assert c.유형코드 == 57
    assert "DED-006" in c.판정근거


def test_type_ilban_is_non_deductible_even_if_nts_says_deductible():
    c = classify_row(_row(국세청="공제", 업태="도매", 유형="일반"), client_type="corporate")
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE
    assert c.유형코드 == 3


def test_unconfirmed_type_falls_back_to_the_nts_column():
    """카면·카영·공란은 담당자가 정한 기준이 없어 예전처럼 국세청 칸을 따른다."""
    c = classify_row(_row(국세청="불공제", 업태="도매", 유형="카면"), client_type="corporate")
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE and "DED-002" in c.판정근거
    c = classify_row(_row(국세청="공제", 업태="도매", 유형=""), client_type="corporate")
    assert c.공제여부 == Deduct.DEDUCTIBLE and "DED-002" in c.판정근거


def test_nts_source_keeps_the_old_behaviour(tmp_path):
    """config 를 국세청_컬럼으로 되돌리면 예전처럼 국세청 불공제가 유형을 일반으로 덮는다."""
    import shutil
    cfg = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", cfg)
    rules = cfg / "rules.yaml"
    rules.write_text(rules.read_text(encoding="utf-8").replace(
        "  source: 유형 ", "  source: 국세청_컬럼 "), encoding="utf-8")
    c = classify_row(_row(국세청="불공제", 업태="도매"), client_type="corporate",
                     config_dir=str(cfg))
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE
    assert c.유형코드 == 3   # 카과(57) → 일반(3) 오버라이드


def test_review_keywords_still_win_over_the_type():
    """유형으로 바꿔도 의심 업종(주유 등) 검토 플래그는 그대로 — 안전장치는 건드리지 않는다."""
    c = classify_row(_row(업태="주유소", 종목="경유", 유형="카과"), client_type="corporate")
    assert c.공제여부 == Deduct.REVIEW and c.needs_review


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


def test_individual_client_uses_withdrawal_account_without_review():
    """[확정 2026-11-23 담당자 통화] 개인사업자 카드 사용분 대변은 인출금(338) — 검토 불필요."""
    c = classify_row(_row(), client_type="individual")
    assert c.대변계정코드 == 338
    assert not any("상대계정" in r for r in c.review_reasons)
    assert "CP-003" in c.판정근거


# ── finalize_reversal 통합 (classify_rows 경유) ─────────────────────────────

def test_negative_unrecommended_finalized_after_recommendation():
    """음수 미추천 행: Phase2 추천 성공 후 finalize_reversal 이 swap 을 확정해야 한다.

    classify_row 는 차변이 미정(미추천)이므로 is_reversal 플래그만 세우고 swap 을 보류.
    classify_rows 가 추천으로 차변을 채운 뒤 finalize_reversal 을 호출 → swap 완료.
    """
    from kafa.cli import classify_rows
    from kafa.recommend.recommender import SeedRecommender
    from kafa.recommend.seed import SeedIndex

    row = _row(차변계정="", 전표상태="미추천", 합계=Decimal("-11000"))
    seed = SeedIndex()
    seed.add("합성가맹점", "000-00-00000", 822)   # 차량유지비(822)를 추천하게 시딩
    classified, _ = classify_rows([row], client_type="corporate",
                                   recommender=SeedRecommender(seed))
    c = classified[0]

    assert c.is_reversal is True
    assert c.reversal_applied is True
    # swap 결과: 원래 대변(미지급비용 262) → 차변, 추천 계정(822) → 대변
    assert c.차변계정코드 == 262
    assert c.대변계정코드 == 822
    assert c.판정유형 == Verdict.RECOMMENDED


def test_negative_unrecommended_no_swap_without_recommendation():
    """음수 미추천 행: 추천 실패 시 방향반전 미확정(플래그만, 차변 여전히 None)."""
    from kafa.cli import classify_rows
    from kafa.recommend.recommender import SeedRecommender
    from kafa.recommend.seed import SeedIndex

    row = _row(차변계정="", 전표상태="미추천", 합계=Decimal("-11000"))
    # 빈 시드 → 추천 실패
    classified, _ = classify_rows([row], client_type="corporate",
                                   recommender=SeedRecommender(SeedIndex()))
    c = classified[0]

    assert c.is_reversal is True        # 음수 플래그 세워짐
    assert c.reversal_applied is False  # 차변 미정이라 swap 보류
    assert c.차변계정코드 is None
    assert c.판정유형 == Verdict.UNRESOLVED


def test_corporate_check_card_keeps_ordinary_deposit_from_download():
    """[확정 2026-11-26 담당자 통화] 법인 체크카드는 대변 보통예금(103). 위하고에 카드를 등록할 때
    체크카드는 상대계정을 보통예금으로 잡아 두므로, 다운로드본 대변 칸이 보통예금이면 지킨다."""
    c = classify_row(_row(대변계정="보통예금"), client_type="corporate")
    assert c.대변계정코드 == 103
    assert "CP-004" in c.판정근거
    assert classify_row(_row(대변계정="미지급비용"), client_type="corporate").대변계정코드 == 262
    assert classify_row(_row(대변계정=""), client_type="corporate").대변계정코드 == 262


def test_individual_counterparty_stays_drawings_even_if_download_says_deposit():
    """개인은 보통예금·현금으로 섞여 있어도 인출금 하나로 바꾼다(2026-11-23 통화)."""
    c = classify_row(_row(대변계정="보통예금"), client_type="individual")
    assert c.대변계정코드 == 338


def test_negative_check_card_row_swaps_with_ordinary_deposit():
    """환불(음수)은 차대변을 뒤집는다 — 체크카드면 보통예금이 차변으로 간다."""
    c = classify_row(_row(대변계정="보통예금", 공급가액=Decimal("-10000"), 세액=Decimal("-1000"),
                          합계=Decimal("-11000")), client_type="corporate")
    assert 103 in (c.차변계정코드, c.대변계정코드)
