"""설정(YAML) 로더. 규칙·계정코드 매핑을 외부 파일에서 읽어 캐싱한다.

규칙/코드표는 코드에 하드코딩하지 않고 config/*.yaml 로 외부화한다(spec §4 Plan-first).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# 기본 config 디렉터리: 패키지 루트의 형제 디렉터리 config/
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# YAML 에서 미확정 자리표시자로 쓰는 문자열 (spec: TODO).
TODO = "TODO"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"설정 파일 형식 오류(딕셔너리 아님): {path}")
    return data


@lru_cache(maxsize=None)
def load_rules(config_dir: str | None = None) -> dict[str, Any]:
    """config/rules.yaml 로드."""
    base = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    return _load_yaml(base / "rules.yaml")


@lru_cache(maxsize=None)
def load_account_codes(config_dir: str | None = None) -> dict[str, int]:
    """config/account_codes.yaml 의 계정명→코드 매핑 로드.

    업로드 양식 '계정과목(참고용)' 시트 파싱분이 있으면 io_wehago.account_sheet
    에서 머지해 사용한다(현재는 시트 미확보 → 검증분만).
    """
    base = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    data = _load_yaml(base / "account_codes.yaml")
    mapping = {str(k): int(v) for k, v in data.get("account_name_to_code", {}).items()}

    # 1.7 (선택) rules.yaml 의 account_sheet_path 가 있으면 계정과목 시트를 머지.
    #     config 검증분이 우선(setdefault). 시트 형식 문제는 무시(검증분만 사용).
    sheet_path = load_rules(config_dir).get("account_sheet_path")
    if sheet_path and not is_todo(sheet_path):
        from kafa.io_wehago.account_sheet import parse_account_sheet  # 지연(순환 방지)
        try:
            for k, v in parse_account_sheet(sheet_path).items():
                mapping.setdefault(str(k), int(v))
        except Exception:  # noqa: BLE001 — 시트 파싱 실패가 매핑 로딩을 막지 않음
            pass
    return mapping


@lru_cache(maxsize=None)
def load_clients(config_dir: str | None = None) -> dict[str, Any]:
    """config/clients.yaml — 수임처 속성(개인/법인·직원 유무). 파일 없으면 기본값만."""
    base = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    path = base / "clients.yaml"
    if not path.exists():
        return {"defaults": {}, "clients": {}}
    data = _load_yaml(path)
    return {"defaults": data.get("defaults") or {},
            "clients": data.get("clients") or {}}


def client_profile(client_id: str | None, config_dir: str | None = None) -> dict:
    """수임처 속성 조회(기본값 위에 개별 설정을 덮어씀).

    자료로는 알 수 없는 사실(직원 유무 등)이라 사람이 clients.yaml 에 적어야 한다.
    미등록 수임처는 기본값을 쓴다.
    """
    cfg = load_clients(config_dir)
    prof = {"client_type": "corporate", "has_employees": True}
    prof.update(cfg.get("defaults") or {})
    if client_id:
        prof.update((cfg.get("clients") or {}).get(client_id) or {})
    return prof


@lru_cache(maxsize=None)
def load_credit_side_codes(config_dir: str | None = None) -> frozenset[int]:
    """대변 계정 코드 집합(차대구분='대변'). 차변계정 추천 후보 제외에 쓴다.

    account_codes.yaml 의 `credit_side_codes` (위하고 계정과목 추출본의 차대구분 유래).
    키가 없으면 빈 집합 — 그 경우 호출측이 보수적으로 동작해야 한다.
    """
    base = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    data = _load_yaml(base / "account_codes.yaml")
    return frozenset(int(c) for c in (data.get("credit_side_codes") or []))


@lru_cache(maxsize=None)
def load_agent(config_dir: str | None = None) -> dict[str, Any]:
    """config/agent.yaml 로드 — 세무대리인 보조 업무(kafa/agent) 설정(율·목록)."""
    base = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    return _load_yaml(base / "agent.yaml")


def is_todo(value: Any) -> bool:
    """값이 미확정 자리표시자(TODO)인지."""
    return isinstance(value, str) and value.strip().upper() == TODO
