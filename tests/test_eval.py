"""정확도 검증 하니스 — 수작업 정답 대조."""
from decimal import Decimal

from kafa.eval import (
    TruthRecord,
    evaluate,
    load_truth_csv,
    match_key,
    render_eval,
)
from kafa.rules.models import ClassifiedRow, Deduct, InputRow, Verdict


def _cls(거래처, bizno, 합계, 품명, 유형코드, 차변, 공제, verdict=Verdict.RULE_CONFIRMED,
         needs_review=False):
    src = InputRow(연도="2026", 일자="03-15", 거래처=거래처, 사업자등록번호=bizno,
                   품명=품명, 합계=Decimal(str(합계)))
    return ClassifiedRow(유형코드=유형코드, 차변계정코드=차변, 대변계정코드=262,
                         공제여부=공제, 판정유형=verdict, needs_review=needs_review,
                         source=src)


def test_match_key_normalizes_bizno():
    assert match_key("111-11-11119", 5500, "커피") == match_key("1111111119", 5500, "커피")


def test_evaluate_all_correct():
    rows = [
        _cls("카페", "1111111119", 5500, "커피", 57, 811, Deduct.DEDUCTIBLE),
        _cls("주유소", "1248100998", 55000, "경유", 57, 822, Deduct.DEDUCTIBLE),
    ]
    truth = {
        match_key("1111111119", 5500, "커피"): TruthRecord(811, 57, "공제"),
        match_key("1248100998", 55000, "경유"): TruthRecord(822, 57, "공제"),
    }
    res = evaluate(rows, truth)
    assert res.matched == 2
    assert res.overall_accuracy == 1.0
    assert res.field_accuracy("차변계정코드") == 1.0
    assert res.automation_rate == 1.0


def test_evaluate_detects_mismatch():
    rows = [_cls("카페", "1111111119", 5500, "커피", 57, 999, Deduct.DEDUCTIBLE)]
    truth = {match_key("1111111119", 5500, "커피"): TruthRecord(811, 57, "공제")}
    res = evaluate(rows, truth)
    assert res.field_accuracy("차변계정코드") == 0.0
    assert res.mismatch_by_field["차변계정코드"] == 1
    assert len(res.mismatches) == 1


def test_unmatched_counted():
    rows = [_cls("카페", "1111111119", 5500, "커피", 57, 811, Deduct.DEDUCTIBLE)]
    res = evaluate(rows, {})       # 정답 없음
    assert res.unmatched == 1
    assert res.matched == 0


def test_needs_review_not_automated():
    rows = [_cls("주유소", "1111111119", 5500, "경유", 57, 822, Deduct.REVIEW,
                 needs_review=True)]
    res = evaluate(rows, {})
    assert res.auto_done == 0
    assert res.automation_rate == 0.0


def test_render_eval_target_flag():
    rows = [_cls("카페", "1111111119", 5500, "커피", 57, 811, Deduct.DEDUCTIBLE)]
    truth = {match_key("1111111119", 5500, "커피"): TruthRecord(811, 57, "공제")}
    text = render_eval(evaluate(rows, truth))
    assert "정확도" in text and "✅" in text


def test_load_truth_csv(tmp_path):
    p = tmp_path / "truth.csv"
    p.write_text("사업자번호,합계,품명,차변계정코드,유형코드,공제여부\n"
                 "111-11-11119,5500,커피,811,57,공제\n", encoding="utf-8-sig")
    truth = load_truth_csv(p)
    rec = truth[match_key("1111111119", "5500", "커피")]
    assert rec.차변계정코드 == 811 and rec.공제여부 == "공제"
