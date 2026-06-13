"""Phase 4 — 검토 리포트 집계/렌더/중간 산출물."""
import csv
from decimal import Decimal

from kafa.report.review import build_review, render_report, write_review_csv
from kafa.rules.models import ClassifiedRow, Deduct, InputRow, Verdict


def _src(거래처="가맹점", bizno="111-11-11119", 공급=Decimal("10000"),
         세액=Decimal("1000")):
    return InputRow(연도="2026", 일자="03-15", 거래처=거래처, 사업자등록번호=bizno,
                    품명="x", 공급가액=공급, 세액=세액, 합계=공급 + 세액)


def _cls(src, **kw):
    base = dict(유형코드=57, 차변계정코드=811, 대변계정코드=262,
                공제여부=Deduct.DEDUCTIBLE, source=src)
    base.update(kw)
    return ClassifiedRow(**base)


def _rows():
    return [
        # 정상
        _cls(_src("정상가맹점")),
        # 미추천 → 추천됨
        _cls(_src("추천가맹점"), 판정유형=Verdict.RECOMMENDED, 신뢰도=0.85,
             추천근거="시드 사업자번호 최빈(85%)"),
        # 검토 플래그
        _cls(_src("주유소"), 공제여부=Deduct.REVIEW, needs_review=True,
             review_reasons=["의심 카테고리: 비영업용승용차"]),
        # 부가율 이상(세액 5%)
        _cls(_src("이상가맹점", 공급=Decimal("10000"), 세액=Decimal("500"))),
        # 미등록 의심(체크섬 무효 사업자번호)
        _cls(_src("의심가맹점", bizno="111-11-11111")),
        # 중복전표 스킵
        _cls(_src("중복가맹점"), skipped=True, skip_reason="중복전표"),
    ]


def test_counts():
    rep = build_review(_rows())
    assert rep.total == 6
    assert rep.skipped == 1
    assert rep.written == 5
    assert rep.recommended == 1
    assert rep.needs_review == 1


def test_recommendation_line_has_basis_and_confidence():
    rep = build_review(_rows())
    assert len(rep.recommendation_lines) == 1
    line = rep.recommendation_lines[0]
    assert "시드" in line and "85%" in line and "811" in line


def test_vat_anomaly_detected():
    rep = build_review(_rows())
    assert any("이상" in ln or "부가율" in ln for ln in rep.vat_anomaly_lines)
    assert len(rep.vat_anomaly_lines) == 1


def test_suspect_vendor_detected():
    rep = build_review(_rows())
    assert any("체크섬" in ln for ln in rep.suspect_vendor_lines)


def test_render_has_sections_and_checkpoint():
    text = render_report(build_review(_rows()))
    assert "검토 리포트" in text
    assert "부가율 이상" in text
    assert "미등록 의심" in text
    assert "체크포인트" in text


def test_automation_rate_kpi():
    rep = build_review(_rows())
    # 작성 5건 중 검토 1건만 사람확인 → 자동 4건
    assert rep.written == 5
    assert rep.needs_human == 1
    assert rep.auto_done == 4
    assert abs(rep.automation_rate - 0.8) < 1e-9
    assert "자동처리율" in render_report(rep)


def test_masking_in_report():
    rep = build_review(_rows(), mask=True)
    joined = "\n".join(rep.review_lines + rep.suspect_vendor_lines)
    assert "*" in joined            # 마스킹 적용
    assert "주유소" not in joined    # 실명 노출 안 됨(마스킹)


def test_write_review_csv(tmp_path):
    path = write_review_csv(_rows(), tmp_path / "r.csv")
    assert path.exists()
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0][0] == "거래일자"
    assert len(rows) == 1 + 6        # 헤더 + 6행(스킵 포함)
    # 마스킹 기본: 거래처 컬럼에 실명 없음
    body = "\n".join(",".join(r) for r in rows[1:])
    assert "정상가맹점" not in body
