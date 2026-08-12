"""Phase 3 — 업로드 .xls 생성: 매핑·필수값·CP949·2MB 분할."""
from decimal import Decimal

import pytest
import xlrd

from kafa.io_wehago.writer import (
    OutputRow,
    to_output_row,
    validate_required,
    write_upload_xls,
)
from kafa.rules.models import ClassifiedRow, Deduct, InputRow


def _classified(**kw):
    src = InputRow(연도="2026", 일자="03-15", 거래처="가맹점",
                   공급가액=Decimal("10000"), 세액=Decimal("1000"),
                   비과세=Decimal("0"), 합계=Decimal("11000"),
                   사업자등록번호="111-11-11111", 품명="간식")
    base = dict(유형코드=57, 차변계정코드=811, 대변계정코드=262,
                공제여부=Deduct.DEDUCTIBLE, source=src)
    base.update(kw)
    return ClassifiedRow(**base)


def test_to_output_row_maps_fields():
    r = to_output_row(_classified())
    assert r.거래일자 == "2026-03-15"
    assert r.유형 == 57
    assert r.차변계정코드 == 811
    assert r.대변계정코드 == 262
    assert r.공제여부 == "공제"
    assert r.거래구분 == ""   # config 기본 공란


def test_review_maps_to_label():
    r = to_output_row(_classified(공제여부=Deduct.REVIEW))
    assert r.공제여부 == "검토"


def test_validate_required_detects_missing():
    rows = [OutputRow(거래일자="", 합계="")]
    errs = validate_required(rows)
    fields = {f for _, f in errs}
    assert "거래일자" in fields and "합계" in fields


def test_write_single_file(tmp_path):
    rows = [to_output_row(_classified())]
    files = write_upload_xls(rows, tmp_path / "out.xls")
    assert len(files) == 1 and files[0].exists()
    bk = xlrd.open_workbook(files[0])
    sh = bk.sheet_by_index(0)
    assert sh.nrows == 2  # 헤더 + 1행
    assert sh.cell_value(1, 0) == "2026-03-15"


def test_strict_raises_on_missing_required(tmp_path):
    bad = [OutputRow(거래일자="", 합계="")]
    with pytest.raises(ValueError):
        write_upload_xls(bad, tmp_path / "bad.xls", strict=True)


def test_2mb_split(tmp_path):
    rows = [to_output_row(_classified()) for _ in range(6)]
    # max_bytes 를 아주 작게 → 행 단위로 분할
    files = write_upload_xls(rows, tmp_path / "big.xls", max_bytes=100)
    assert len(files) > 1
    assert all(f.exists() for f in files)
    assert all("_part" in f.name for f in files)


def test_cp949_sanitize(tmp_path):
    # CP949 불가 문자(이모지) 포함 거래처 — 예외 없이 작성돼야 함
    src = InputRow(연도="2026", 일자="03-15", 거래처="가맹점😀",
                   합계=Decimal("11000"), 품명="x")
    cls = ClassifiedRow(유형코드=3, 차변계정코드=811, 대변계정코드=262,
                        공제여부=Deduct.DEDUCTIBLE, source=src)
    files = write_upload_xls([to_output_row(cls)], tmp_path / "emoji.xls")
    assert files[0].exists()
    bk = xlrd.open_workbook(files[0])
    assert bk.sheet_by_index(0).nrows == 2


def test_header_matches_real_template():
    """생성 헤더는 실제 양식 문구와 정확히 같아야 업로드가 인식된다."""
    import xlrd
    from kafa.io_wehago.schema import OUTPUT_HEADERS
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "o.xls"
        write_upload_xls([OutputRow(거래일자="2026-01-02", 합계=1100)], p, strict=False)
        head = xlrd.open_workbook(p).sheet_by_index(0).row_values(0)
    assert head == OUTPUT_HEADERS
    assert head[1] == "거래처(가맹점명)"      # 양식 문구(필드명 '거래처'와 다름)
