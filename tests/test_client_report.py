"""고객 제공용 요약 리포트."""
from decimal import Decimal

from kafa.report.client_report import build_client_report
from kafa.rules.models import ClassifiedRow, Deduct, InputRow


def _row(거래처, 품명, 공급, 세액, 유형코드, 공제, 일자="03-15", bizno="111-11-11119",
         needs_review=False, reasons=None, verdict=None, 차변=811, skipped=False):
    src = InputRow(연도="2026", 일자=일자, 거래처=거래처, 품명=품명, 업태="",
                   공급가액=Decimal(str(공급)), 세액=Decimal(str(세액)),
                   합계=Decimal(str(공급 + 세액)), 사업자등록번호=bizno)
    from kafa.rules.models import Verdict
    return ClassifiedRow(유형코드=유형코드, 차변계정코드=차변, 대변계정코드=262,
                         공제여부=공제, source=src, needs_review=needs_review,
                         review_reasons=reasons or [],
                         판정유형=verdict or Verdict.RULE_CONFIRMED,
                         skipped=skipped, skip_reason="중복전표" if skipped else "")


def _rows():
    from kafa.rules.models import Verdict
    return [
        _row("카페", "커피", 10000, 1000, 57, Deduct.DEDUCTIBLE),
        _row("주유소", "경유", 50000, 5000, 57, Deduct.REVIEW, 일자="03-16",
             needs_review=True, reasons=["의심 카테고리: 비영업용승용차"]),
        _row("미정상점", "비품", 20000, 2000, 57, Deduct.DEDUCTIBLE, 일자="03-17",
             verdict=Verdict.UNRESOLVED, 차변=None),
        _row("중복", "커피", 9999, 999, 57, Deduct.DEDUCTIBLE, skipped=True),
    ]


def test_has_sections_and_counts():
    txt = build_client_report(_rows(), period_label="3월")
    assert "3월" in txt
    assert "처리 현황" in txt
    assert "총 3건" in txt                      # 스킵 제외
    assert "부가세 관점" in txt
    assert "고객님 확인 요청" in txt


def test_attention_items_client_language():
    txt = build_client_report(_rows())
    # 주유소(비영업용) → 차량 확인 안내, 미정상점(미추천) → 용도 확인 안내
    assert "차량" in txt
    assert "용도" in txt
    # 날짜·금액으로 식별 가능
    assert "03-16" in txt and "55,000원" in txt


def test_vendor_masked_by_default():
    txt = build_client_report(_rows())
    assert "주유*" in txt or "*" in txt          # 거래처 마스킹
    assert "주유소" not in txt                    # 실명 노출 안 됨


def test_no_attention_when_clean():
    rows = [_row("카페", "커피", 10000, 1000, 57, Deduct.DEDUCTIBLE)]
    txt = build_client_report(rows)
    assert "특별히 확인이 필요한 항목은 없습니다" in txt
