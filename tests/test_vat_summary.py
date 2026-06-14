"""부가세 신고 보조 집계."""
from decimal import Decimal

from kafa.report.vat_summary import build_vat_summary, render_vat_summary
from kafa.rules.models import ClassifiedRow, Deduct, InputRow


def _row(공급, 세액, 유형코드, 공제, 의제=False, 면세매입=0, skipped=False):
    src = InputRow(거래처="가맹점", 공급가액=Decimal(str(공급)), 세액=Decimal(str(세액)))
    return ClassifiedRow(유형코드=유형코드, 공제여부=공제, 의제대상여부=의제,
                         면세매입액=Decimal(str(면세매입)), source=src,
                         skipped=skipped, skip_reason="중복전표" if skipped else "")


def _rows():
    return [
        _row(10000, 1000, 57, Deduct.DEDUCTIBLE),                 # 과세 공제
        _row(20000, 2000, 57, Deduct.DEDUCTIBLE),                 # 과세 공제
        _row(5000, 500, 3, Deduct.NON_DEDUCTIBLE),                # 불공제
        _row(8000, 0, 58, Deduct.DEDUCTIBLE, 의제=True, 면세매입=8000),  # 면세+의제
        _row(3000, 300, 57, Deduct.REVIEW),                       # 검토
        _row(9999, 999, 57, Deduct.DEDUCTIBLE, skipped=True),     # 스킵(제외)
    ]


def test_aggregates():
    s = build_vat_summary(_rows())
    assert s.written == 5                          # 스킵 제외
    assert s.과세공제_건수 == 2
    assert s.과세공제_공급가액 == Decimal("30000")
    assert s.공제_매입세액 == Decimal("3000")
    assert s.불공제_건수 == 1 and s.불공제_세액 == Decimal("500")
    assert s.면세_건수 == 1 and s.면세_금액 == Decimal("8000")
    assert s.의제대상_건수 == 1 and s.의제대상_면세매입액 == Decimal("8000")
    assert s.검토_건수 == 1 and s.검토_공급가액 == Decimal("3000")


def test_render_has_sections():
    text = render_vat_summary(build_vat_summary(_rows()))
    assert "부가세 신고 보조" in text
    assert "공제매입세액" in text
    assert "의제대상" in text
    assert "확인 필요" in text


def test_empty():
    s = build_vat_summary([])
    assert s.written == 0 and s.공제_매입세액 == Decimal(0)
