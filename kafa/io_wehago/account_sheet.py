"""업로드 양식의 '계정과목(참고용)' 시트 → 계정명→코드 매핑 자동 생성.

시트가 확보되면 본 파서가 **구조(헤더)** 기준으로 계정명·코드 컬럼을 찾아 매핑을 만든다.
특정 값을 하드코딩/추측하지 않으므로, 어떤 계정과목 시트가 들어와도 그대로 파싱한다.
생성된 매핑은 `build_mapping` 에서 config 의 검증분과 머지한다(config 우선).
"""
from __future__ import annotations

from pathlib import Path

from kafa.config_loader import load_account_codes

# 컬럼 헤더 탐지 키워드(공백 제거 후 부분일치)
_CODE_KEYS = ("계정코드", "코드")
_NAME_KEYS = ("계정과목", "계정명", "계정")


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


def parse_account_sheet(path: str | Path, sheet_name: str = "계정과목") -> dict[str, int]:
    """양식 .xls/.xlsx 의 계정과목 시트를 읽어 {계정명: 코드} 생성.

    - 시트 선택: 이름에 '계정과목'(또는 sheet_name)이 포함된 시트, 없으면 첫 시트.
    - 컬럼 탐지: 헤더에 '코드'가 든 컬럼=코드, '계정과목/계정명/계정'이 든 컬럼=계정명.
    - 코드가 정수로 파싱되지 않는 행은 건너뛴다(소계/머리글 등).
    계정명/코드 컬럼을 못 찾으면 ValueError.
    """
    import pandas as pd

    xls = pd.ExcelFile(path)
    target = next((s for s in xls.sheet_names
                   if sheet_name in str(s) or "계정과목" in str(s)), None)
    if target is None:
        target = xls.sheet_names[0]

    df = xls.parse(target, dtype=object)   # 핸들 재사용(파일 재오픈 없음)
    df.columns = [str(c).strip() for c in df.columns]

    code_col = _pick(df.columns, _CODE_KEYS)
    name_col = _pick(df.columns, _NAME_KEYS, exclude=code_col)
    if name_col is None or code_col is None:
        raise ValueError(
            f"계정과목 시트에서 계정명/코드 컬럼을 찾지 못함: {list(df.columns)}")

    mapping: dict[str, int] = {}
    for _, r in df.iterrows():
        raw_name = r[name_col]
        name = "" if raw_name is None else str(raw_name).strip()
        if not name or name.lower() == "nan":
            continue
        raw_code = r[code_col]
        try:
            code = int(float(str(raw_code).replace(",", "").strip()))
        except (ValueError, TypeError):
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
