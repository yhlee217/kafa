"""통합 — process_rows 가 기장→신고→보고 산출물 일습을 생성하는지."""
from decimal import Decimal
from pathlib import Path

from kafa.cli import process_rows
from kafa.rules.models import InputRow


def _rows():
    return [
        InputRow(연도="2026", 일자="03-15", 거래처="카페", 품명="커피", 업태="음식점",
                 공급가액=Decimal("10000"), 세액=Decimal("1000"), 합계=Decimal("11000"),
                 국세청="공제", 유형="카과", 차변계정="(판)복리후생비",
                 전표상태="", 사업자등록번호="124-81-00998"),
        InputRow(연도="2026", 일자="03-16", 거래처="주유소", 품명="경유", 업태="주유소",
                 공급가액=Decimal("50000"), 세액=Decimal("5000"), 합계=Decimal("55000"),
                 국세청="공제", 유형="카과", 차변계정="(판)차량유지비",
                 전표상태="", 사업자등록번호="111-11-11111"),
        InputRow(연도="2026", 일자="03-17", 거래처="중복", 품명="비품",
                 공급가액=Decimal("3000"), 세액=Decimal("300"), 합계=Decimal("3300"),
                 국세청="공제", 유형="카과", 차변계정="(판)복리후생비",
                 전표상태="중복전표", 사업자등록번호="124-81-00998"),
    ]


def test_pipeline_produces_all_artifacts(tmp_path):
    out = tmp_path / "3월_upload.xls"
    res = process_rows(_rows(), out, client_type="corporate")

    # 업로드본 + 모든 보조 산출물
    assert res["files"] and all(Path(p).exists() for p in res["files"])
    stem = out.with_name(out.stem)  # .../3월_upload
    for suffix in ("_review.txt", "_review.csv", "_vat.txt", "_vat.csv",
                   "_client.txt", "_risk.txt"):
        assert Path(str(stem) + suffix).exists(), suffix

    # 스킵(중복) 제외하고 2건 작성
    assert res["written"] == 2 and res["skipped"] == 1

    # 핵심 내용 확인(거래처 실명 미노출 — 마스킹)
    client_txt = Path(str(stem) + "_client.txt").read_text(encoding="utf-8")
    assert "처리 현황" in client_txt and "주유소" not in client_txt
    vat_txt = Path(str(stem) + "_vat.txt").read_text(encoding="utf-8")
    assert "부가세 신고 보조 집계" in vat_txt
    risk_txt = Path(str(stem) + "_risk.txt").read_text(encoding="utf-8")
    assert "증빙·리스크 점검" in risk_txt and "중복전표" in risk_txt
