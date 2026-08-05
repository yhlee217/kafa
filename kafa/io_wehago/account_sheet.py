"""업로드 양식의 '계정과목(참고용)' 시트 → 계정명→코드 매핑 자동 생성.

실제 양식(2026-08 확보)은 분류별로 **(코드, 계정과목) 쌍이 가로로 반복**되는 구조다.

    [행 n-1]  자산 |      | 부채 |      | … | 제조 |      | 도급 |      | 판관비 |
    [행 n  ]  코드 | 계정과목 | 코드 | 계정과목 | … (반복)
    [행 n+1]  101  | 현   금  | 251  | 외상매입금 | …

분류가 원가구분이면 다운로드본 표기에 맞춰 접두 마커를 붙인다
(제조→(제) · 도급→(도) · 분양→(분) · 판관비→(판), 자산/부채/매출/영업외는 접두 없음).
담당자 확인(복리후생비 = 제조 511 / 도급 611 / 판관 811)과 일치함을 검증했다.

단순 구조(코드·계정과목 컬럼 1쌍)도 그대로 지원한다. 값을 추측하지 않고
**구조(헤더)** 만 보고 파싱하므로 양식이 바뀌어도 동작한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from kafa.config_loader import load_account_codes

# 컬럼 헤더 탐지 키워드(공백 제거 후 부분일치)
_CODE_KEYS = ("계정코드", "코드")
_NAME_KEYS = ("계정과목", "계정명", "계정")

# 분류(시트 헤더) → 다운로드본 계정명 접두 마커. 원가구분만 마커를 갖는다.
_CATEGORY_MARKER = {"제조": "제", "도급": "도", "분양": "분", "판관비": "판"}

_WS = re.compile(r"\s+")


def _norm(v) -> str:
    """셀 값 → 공백 제거 문자열('현    금' → '현금')."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else _WS.sub("", s)


def _to_code(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _pick(columns, keywords, exclude=None):
    """keywords 우선순위 순으로 가장 구체적인 헤더를 먼저 매칭한다.

    예: '계정코드'를 일반 '코드'보다 먼저 골라, '관리코드' 같은 다른 코드 컬럼 오인을 줄인다.
    """
    norm = [(c, str(c).replace(" ", "")) for c in columns if c != exclude]
    for k in keywords:
        for c, cl in norm:
            if k in cl:
                return c
    return None


def _find_pair_header(raw) -> tuple[int, list[int]] | None:
    """(코드, 계정과목)이 가로로 반복되는 헤더 행을 찾는다 → (행 index, 코드열 목록)."""
    for i in range(min(len(raw), 30)):        # 상단 30행 안에서 탐색
        row = raw.iloc[i]
        cols = [j for j in range(len(row) - 1)
                if _norm(row.iloc[j]) in _CODE_KEYS
                and any(k in _norm(row.iloc[j + 1]) for k in _NAME_KEYS)]
        if cols:
            return i, cols
    return None


def _parse_pairs(raw, header_i: int, code_cols: list[int]) -> dict[str, int]:
    """분류별 (코드, 계정과목) 쌍 반복 구조를 파싱."""
    cat_row = raw.iloc[header_i - 1] if header_i > 0 else None
    mapping: dict[str, int] = {}
    for c in code_cols:
        category = _norm(cat_row.iloc[c]) if cat_row is not None else ""
        marker = _CATEGORY_MARKER.get(category, "")
        prefix = f"({marker})" if marker else ""
        for i in range(header_i + 1, len(raw)):
            row = raw.iloc[i]
            code = _to_code(row.iloc[c])
            name = _norm(row.iloc[c + 1])
            if code is None or not name:
                continue
            mapping[prefix + name] = code
    return mapping


def parse_account_sheet(path: str | Path, sheet_name: str = "계정과목") -> dict[str, int]:
    """양식 .xls/.xlsx 의 계정과목 시트를 읽어 {계정명: 코드} 생성.

    - 시트 선택: 이름에 '계정과목'(또는 sheet_name)이 포함된 시트, 없으면 첫 시트.
    - 분류별 (코드, 계정과목) 쌍이 반복되면 분류 접두((제)/(도)/(분)/(판))를 붙여 매핑.
    - 그런 헤더가 없으면 단순 구조(코드·계정과목 컬럼 1쌍)로 파싱.
    계정명/코드 컬럼을 못 찾으면 ValueError.
    """
    import pandas as pd

    xls = pd.ExcelFile(path)
    target = next((s for s in xls.sheet_names
                   if sheet_name in str(s) or "계정과목" in str(s)), None)
    if target is None:
        target = xls.sheet_names[0]

    # 1) 실제 양식: 헤더 위치를 모르므로 원본 그대로 읽어 구조를 찾는다.
    raw = xls.parse(target, dtype=object, header=None)
    found = _find_pair_header(raw)
    if found:
        header_i, code_cols = found
        mapping = _parse_pairs(raw, header_i, code_cols)
        if mapping:
            return mapping

    # 2) 단순 구조 폴백: 첫 행을 헤더로 보고 컬럼명으로 탐지.
    df = xls.parse(target, dtype=object)
    df.columns = [str(c).strip() for c in df.columns]
    code_col = _pick(df.columns, _CODE_KEYS)
    name_col = _pick(df.columns, _NAME_KEYS, exclude=code_col)
    if name_col is None or code_col is None:
        raise ValueError(
            f"계정과목 시트에서 계정명/코드 컬럼을 찾지 못함: {list(df.columns)}")

    mapping = {}
    for _, r in df.iterrows():
        name = _norm(r[name_col])
        code = _to_code(r[code_col])
        if not name or code is None:
            continue                      # 숫자 아닌 행(소계/구분선 등) 제외
        mapping[name] = code
    return mapping


def build_mapping(sheet_path: str | Path | None = None,
                  *, config_dir: str | None = None) -> dict[str, int]:
    """검증된 config 매핑 + (있으면)시트 파싱 매핑을 머지. config 우선."""
    mapping = dict(load_account_codes(config_dir))
    if sheet_path:
        try:
            sheet_map = parse_account_sheet(sheet_path)
        except Exception:                 # noqa: BLE001 — 시트 형식 문제로 전체가 막히지 않게
            sheet_map = {}
        for k, v in sheet_map.items():    # config(검증분)를 우선으로 보강
            mapping.setdefault(k, v)
    return mapping
