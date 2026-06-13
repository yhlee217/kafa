"""입력 형식 견고성 — 위하고 파일 아닌 경우 명확한 에러."""
import openpyxl
import pytest

from kafa.io_wehago.reader import InputFormatError, read_download_xlsx


def _xlsx(tmp_path, header, rows=()):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    p = tmp_path / "f.xlsx"
    wb.save(p)
    return p


def test_missing_required_columns_raises(tmp_path):
    p = _xlsx(tmp_path, ["엉뚱", "컬럼"])
    with pytest.raises(InputFormatError) as e:
        read_download_xlsx(p)
    assert "필수 컬럼" in str(e.value)


def test_valid_header_ok(tmp_path):
    header = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액",
              "비과세", "합계", "국세청", "업태", "종목", "유형", "차변계정",
              "대변계정", "관리", "전표상태", "사업자등록번호"]
    p = _xlsx(tmp_path, header,
              [["2026", "03-15", "A", "가맹점", "법인", "커피", 5000, 500, 0, 5500,
                "공제", "음식점", "카페", "카과", "(판)복리후생비", "", "", "", "111-11-11119"]])
    rows = read_download_xlsx(p)
    assert len(rows) == 1
    assert rows[0].거래처 == "가맹점"
