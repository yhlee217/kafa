"""업로드 양식의 '계정과목(참고용)' 시트 → 계정명→코드 매핑 자동 생성.

[보류] 시트 파일 미확보. 시트 확보 시 본 파서로 매핑을 자동 생성해
config/account_codes.yaml 의 검증분과 머지한다(1.7).

지금은 인터페이스(골격)만 둔다. 시트 컬럼 구조 확인 후 본문 구현.
"""
from __future__ import annotations

from pathlib import Path

from kafa.config_loader import load_account_codes


def parse_account_sheet(path: str | Path, sheet_name: str = "계정과목") -> dict[str, int]:
    """양식 .xls/.xlsx 의 계정과목 시트를 읽어 {계정명: 코드} 생성.

    TODO: 시트 확보 후 컬럼(계정명/계정코드) 위치 확정해 구현.
    """
    raise NotImplementedError(
        "계정과목(참고용) 시트 미확보([보류]). 시트 확보 후 컬럼 구조 확정하여 구현."
    )


def build_mapping(sheet_path: str | Path | None = None,
                  *, config_dir: str | None = None) -> dict[str, int]:
    """검증된 config 매핑 + (있으면)시트 파싱 매핑을 머지. 시트 우선 아님 — config 우선."""
    mapping = dict(load_account_codes(config_dir))
    if sheet_path:
        try:
            sheet_map = parse_account_sheet(sheet_path)
        except NotImplementedError:
            sheet_map = {}
        # config(검증분)를 우선으로 머지
        for k, v in sheet_map.items():
            mapping.setdefault(k, v)
    return mapping
