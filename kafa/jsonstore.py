"""로컬 JSON 저장 헬퍼 — 멱등 가드/기준선 등에서 공용.

손상/부재 파일은 기본값으로 회복하고, 저장 시 부모 디렉터리를 만든다.
(dup_guard.DupGuard, agent.recon.VendorBaseline 가 공유.)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path, default: Any) -> Any:
    """JSON 로드. 파일이 없거나 깨졌으면 default 반환(예외 없음)."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str | Path, obj: Any) -> None:
    """JSON 저장. 부모 디렉터리 자동 생성."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
