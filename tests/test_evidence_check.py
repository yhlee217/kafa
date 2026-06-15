"""증빙·리스크 점검."""
from decimal import Decimal

from kafa.report.evidence_check import (
    CAT_검토,
    CAT_미등록,
    CAT_부가율,
    CAT_불공제,
    CAT_중복,
    build_evidence_check,
    render_evidence_check,
)
from kafa.rules.models import ClassifiedRow, Deduct, InputRow


def _row(공급, 세액, 공제, bizno="111-11-11119", reasons=None, skipped=False,
         skip_reason="", 거래처="가맹점", 품명="품목", 일자="03-15"):
    src = InputRow(연도="2026", 일자=일자, 거래처=거래처, 품명=품명,
                   공급가액=Decimal(str(공급)), 세액=Decimal(str(세액)),
                   합계=Decimal(str(공급 + 세액)), 사업자등록번호=bizno)
    return ClassifiedRow(유형코드=57, 차변계정코드=811, 대변계정코드=262, 공제여부=공제,
                         review_reasons=reasons or [], source=src,
                         skipped=skipped, skip_reason=skip_reason)


def test_counts_and_categories():
    rows = [
        _row(10000, 1000, Deduct.DEDUCTIBLE),                                   # 정상 공제
        _row(5000, 500, Deduct.NON_DEDUCTIBLE, reasons=["면세사업관련"]),         # 불공제
        _row(50000, 5000, Deduct.REVIEW, reasons=["의심 카테고리: 비영업용승용차"]),  # 검토
        _row(10000, 1000, Deduct.DEDUCTIBLE, bizno="111-11-11111"),             # 미등록 의심
        _row(10000, 500, Deduct.DEDUCTIBLE),                                    # 부가율 이상(5%)
        _row(9999, 999, Deduct.DEDUCTIBLE, skipped=True, skip_reason="중복전표"),  # 중복
    ]
    rep = build_evidence_check(rows)
    cats = rep.by_category()
    assert rep.written == 5                      # 중복 제외
    assert rep.적격증빙_건수 == 5
    assert len(cats[CAT_불공제]) == 1
    assert len(cats[CAT_검토]) == 1
    assert len(cats[CAT_미등록]) == 1
    assert len(cats[CAT_부가율]) == 1
    assert len(cats[CAT_중복]) == 1


def test_render_and_masking():
    rows = [_row(10000, 1000, Deduct.DEDUCTIBLE, bizno="111-11-11111", 거래처="비밀상호")]
    txt = render_evidence_check(build_evidence_check(rows))
    assert "적격증빙" in txt
    assert "미등록" in txt
    assert "비밀상호" not in txt                  # 거래처 마스킹


def test_clean_no_flags():
    rep = build_evidence_check([_row(10000, 1000, Deduct.DEDUCTIBLE)])
    assert rep.flags == []
    txt = render_evidence_check(rep)
    assert "점검 필요 항목 없음" in txt
    assert "0건" not in txt                        # 깔끔한 "없음" 메시지 — "0건:" 중복 표기 없어야 함


def test_render_flag_count_shown_when_present():
    rows = [_row(10000, 1000, Deduct.DEDUCTIBLE, bizno="111-11-11111")]
    txt = render_evidence_check(build_evidence_check(rows))
    assert "점검 필요 1건:" in txt                  # 플래그 있을 때는 건수 표기
