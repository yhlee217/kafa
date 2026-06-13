"""서비스 파사드 — preview / convert / 마스킹·결정론 보장."""
import json

import openpyxl
import xlrd

from kafa.service import convert, preview

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path, rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _valid(tmp_path):
    return _xlsx(tmp_path / "in.xlsx", [
        ["2026", "03-15", "A", "카페", "법인", "커피", 5000, 500, 0, 5500,
         "공제", "음식점", "카페", "카과", "(판)복리후생비", "", "", "", "111-11-11119"],
        ["2026", "03-25", "A", "카페", "법인", "디저트", 7000, 700, 0, 7700,
         "공제", "음식점", "카페", "", "", "", "", "미추천", "111-11-11119"],
    ])


def test_preview_valid(tmp_path):
    p = preview(_valid(tmp_path))
    assert p["ok"] is True
    assert p["row_count"] == 2
    assert p["unrecommended"] == 1
    # 거래처 샘플은 마스킹
    assert all("*" in s for s in p["vendor_sample_masked"])
    assert "카페" not in "".join(p["vendor_sample_masked"]) or \
           all(s.startswith("카") for s in p["vendor_sample_masked"])


def test_preview_invalid_file(tmp_path):
    bad = tmp_path / "bad.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["엉뚱", "컬럼"]); wb.save(bad)
    p = preview(bad)
    assert p["ok"] is False
    assert "필수 컬럼" in p["error"]


def test_convert_summary(tmp_path):
    res = convert(_valid(tmp_path), tmp_path / "out", client_type="corporate")
    assert res["ok"] is True
    assert len(res["files"]) == 1
    fr = res["files"][0]
    assert fr["written"] == 2
    assert "in_upload.xls" in fr["output_files"]
    assert (tmp_path / "out" / "in_upload.xls").exists()
    assert "guidance" in res and res["guidance"]


def test_convert_returns_no_raw_pii(tmp_path):
    # 고유 상호/무효 사업자번호 → 검토·미등록 의심 라인에 등장하지만 마스킹돼야 함
    _xlsx(tmp_path / "in.xlsx", [
        ["2026", "03-15", "A", "유니크상호명칭", "법인", "비품", 10000, 500, 0, 10500,
         "공제", "도매", "문구", "", "", "", "", "미추천", "111-11-11111"],
    ])
    res = convert(tmp_path / "in.xlsx", tmp_path / "out")
    blob = json.dumps(res, ensure_ascii=False)
    assert "유니크상호명칭" not in blob          # 실명 노출 금지
    assert "111-11-11111" not in blob            # 사업자번호 평문 노출 금지
    assert "*" in blob                           # 마스킹 흔적 존재


def _read_cells(xls_path):
    sh = xlrd.open_workbook(xls_path).sheet_by_index(0)
    return [sh.row_values(r) for r in range(sh.nrows)]


def test_determinism_same_output(tmp_path):
    src = _valid(tmp_path)
    convert(src, tmp_path / "o1")
    convert(src, tmp_path / "o2")
    a = _read_cells(tmp_path / "o1" / "in_upload.xls")
    b = _read_cells(tmp_path / "o2" / "in_upload.xls")
    assert a == b                                # 동일 입력 → 동일 출력(오차 없음)
