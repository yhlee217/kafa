"""계정과목(참고용) 시트 파서 — 구조(헤더) 기반. 합성 시트만 사용."""
import openpyxl
import pytest

from kafa.io_wehago.account_sheet import build_mapping, parse_account_sheet


def _sheet(path, header, rows, sheet_title="계정과목(참고용)"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_parse_basic(tmp_path):
    p = _sheet(tmp_path / "form.xlsx", ["계정과목", "계정코드"],
               [["미지급비용", 262], ["(판)복리후생비", 811], ["(판)차량유지비", 822]])
    m = parse_account_sheet(p)
    assert m == {"미지급비용": 262, "(판)복리후생비": 811, "(판)차량유지비": 822}


def test_header_variants_and_skip_nonnumeric(tmp_path):
    # 헤더가 '계정명'/'코드' 이고, 코드가 숫자 아닌 머리글/소계 행은 건너뛴다
    p = _sheet(tmp_path / "form2.xlsx", ["계정명", "코드"],
               [["구분", "코드"],            # 머리글 반복 → 코드 비숫자 → 제외
                ["현금", "101"],
                ["소계", ""],                # 코드 없음 → 제외
                ["외상매입금", 251]])
    m = parse_account_sheet(p)
    assert m == {"현금": 101, "외상매입금": 251}


def test_picks_named_sheet_among_many(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "표지"
    wb.active.append(["아무거나", "값"])
    ws2 = wb.create_sheet("계정과목")
    ws2.append(["계정과목", "계정코드"])
    ws2.append(["선급금", 131])
    p = tmp_path / "multi.xlsx"
    wb.save(p)
    assert parse_account_sheet(p) == {"선급금": 131}


def test_code_column_priority_over_decoy(tmp_path):
    # '관리코드' 디코이가 있어도 '계정코드'를 우선 선택해야 한다
    p = _sheet(tmp_path / "form_decoy.xlsx", ["계정과목", "관리코드", "계정코드"],
               [["현금", "A1", 101], ["미지급비용", "B2", 262]])
    assert parse_account_sheet(p) == {"현금": 101, "미지급비용": 262}


def test_missing_columns_raises(tmp_path):
    p = _sheet(tmp_path / "bad.xlsx", ["이름", "값"], [["x", 1]])
    with pytest.raises(ValueError):
        parse_account_sheet(p)


def test_build_mapping_merges_config_first(tmp_path):
    # 시트가 같은 계정에 다른 코드를 줘도 config(검증분)가 우선
    p = _sheet(tmp_path / "form3.xlsx", ["계정과목", "계정코드"],
               [["미지급비용", 999], ["임차료", 819]])
    m = build_mapping(p)
    assert m["미지급비용"] == 262          # config 우선(999 무시)
    assert m["임차료"] == 819              # 시트에서 보강된 신규 계정


def test_build_mapping_bad_sheet_is_safe(tmp_path):
    p = _sheet(tmp_path / "bad2.xlsx", ["이름", "값"], [["x", 1]])
    m = build_mapping(p)                    # 컬럼 못 찾아도 예외 없이 config만
    assert m["미지급비용"] == 262
