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
