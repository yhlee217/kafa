"""수임처 속성 조사표 — 양식 생성 / 파싱 / YAML 변환."""
import openpyxl
import pytest

from kafa.clients import COLUMNS, SHEET, parse_template, to_yaml, write_template


def test_template_has_sheets_and_header(tmp_path):
    p = write_template(tmp_path / "t.xlsx", ["고객001", "고객002"])
    wb = openpyxl.load_workbook(p)
    assert SHEET in wb.sheetnames and "작성방법" in wb.sheetnames
    ws = wb[SHEET]
    assert [c.value for c in ws[1]] == COLUMNS
    assert [ws.cell(row=r, column=1).value for r in (2, 3)] == ["고객001", "고객002"]


def _filled(tmp_path, rows):
    p = write_template(tmp_path / "f.xlsx")
    wb = openpyxl.load_workbook(p)
    ws = wb[SHEET]
    for r in rows:
        ws.append(r)
    wb.save(p)
    return p


def test_parse_maps_korean_answers(tmp_path):
    p = _filled(tmp_path, [
        ["1인사업자", "개인", "아니오", "카페", "혼자 운영"],
        ["행복상사", "법인", "예", "", ""],
    ])
    got = parse_template(p)
    assert got["1인사업자"]["client_type"] == "individual"
    assert got["1인사업자"]["has_employees"] is False
    assert "카페" in got["1인사업자"]["note"]
    assert got["행복상사"] == {"client_type": "corporate", "has_employees": True}


def test_parse_tolerates_variants_and_blanks(tmp_path):
    p = _filled(tmp_path, [
        ["A", "법인사업자", "있음", "", ""],
        ["B", "", "", "", ""],            # 미기재 → 빈 속성(기본값 사용)
        ["", "개인", "예", "", ""],        # 이름 없음 → 무시
    ])
    got = parse_template(p)
    assert got["A"]["has_employees"] is True and got["A"]["client_type"] == "corporate"
    assert got["B"] == {}
    assert "" not in got


def test_to_yaml_roundtrips(tmp_path):
    import yaml
    text = to_yaml({"고객001": {"client_type": "individual", "has_employees": False}})
    data = yaml.safe_load(text)
    assert data["defaults"]["client_type"] == "corporate"
    assert data["clients"]["고객001"]["has_employees"] is False
    # config_loader 가 그대로 읽을 수 있어야 한다
    d = tmp_path / "config"; d.mkdir()
    (d / "clients.yaml").write_text(text, encoding="utf-8")
    from kafa.config_loader import client_profile, load_clients
    load_clients.cache_clear()
    prof = client_profile("고객001", str(d))
    assert prof["client_type"] == "individual" and prof["has_employees"] is False
    load_clients.cache_clear()


# ── 이름 미리 채우기 (담당자가 타이핑하지 않게) ──

def test_names_from_excel_finds_column_by_header(tmp_path):
    """헤더 위치·컬럼 순서를 몰라도 이름 컬럼을 구조로 찾는다."""
    p = tmp_path / "거래처목록.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["거래처 목록 (2026년)"])          # 제목행
    ws.append([])
    ws.append(["코드", "거래처명", "사업자번호"])  # 실제 헤더
    ws.append(["001", "행복상사", "111-11-11119"])
    ws.append(["002", "대박유통", "222-22-22228"])
    ws.append(["", "합계", ""])                   # 집계행 → 제외
    ws.append(["003", "행복상사", ""])            # 중복 → 제외
    wb.save(p)
    from kafa.clients import names_from_excel
    assert names_from_excel(p) == ["행복상사", "대박유통"]


def test_names_from_excel_ignores_sheet_without_name_column(tmp_path):
    p = tmp_path / "x.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["금액", "일자"]); ws.append([1000, "01-01"])
    wb.save(p)
    from kafa.clients import names_from_excel
    assert names_from_excel(p) == []


def test_names_from_inbox_uses_folder_names(tmp_path):
    inbox = tmp_path / "inbox"
    for n in ("고객002", "고객001", "_archive"):
        (inbox / n).mkdir(parents=True)
    from kafa.clients import names_from_inbox
    assert names_from_inbox(inbox) == ["고객001", "고객002"]   # _로 시작하면 제외


def test_template_prefills_names(tmp_path):
    p = write_template(tmp_path / "t.xlsx", ["행복상사", "대박유통"])
    ws = openpyxl.load_workbook(p)[SHEET]
    assert [ws.cell(row=r, column=1).value for r in (2, 3)] == ["행복상사", "대박유통"]
    # 이름만 채우고 나머지는 비워 둔다(담당자가 고르도록)
    assert ws.cell(row=2, column=2).value in (None, "")
