"""루프 스펙(YAML) 로더 — config/loops/*.yaml → LoopSpec.

규칙/코드표와 마찬가지로 프롬프트·루브릭도 코드에 박지 않고 외부화한다.
새 주제는 config/loops/<name>.yaml 하나만 추가하면 된다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kafa.loop.models import LoopSpec

# 기본 루프 디렉터리: 프로젝트 루트의 config/loops/
_DEFAULT_LOOPS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "loops"


def loop_spec_from_dict(data: dict[str, Any]) -> LoopSpec:
    """딕셔너리 → LoopSpec. 누락 필드는 LoopSpec 기본값을 따른다."""
    return LoopSpec(
        name=str(data["name"]),
        role=str(data["role"]),
        task=str(data["task"]),
        output_format=str(data.get("output_format", "")),
        rubric=[str(r) for r in (data.get("rubric") or [])],
        max_iter=int(data.get("max_iter", 4)),
        pass_score=int(data.get("pass_score", 90)),
        actor_effort=data.get("actor_effort"),
        evaluator_effort=data.get("evaluator_effort", "low"),
        actor_model=data.get("actor_model"),
        evaluator_model=data.get("evaluator_model"),
    )


def load_loop_spec(name: str, *, loops_dir: str | None = None) -> LoopSpec:
    """config/loops/<name>.yaml 을 읽어 LoopSpec 으로 반환."""
    base = Path(loops_dir) if loops_dir else _DEFAULT_LOOPS_DIR
    path = base / f"{name}.yaml"
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"루프 스펙 형식 오류(딕셔너리 아님): {path}")
    return loop_spec_from_dict(data)


def available_loops(loops_dir: str | None = None) -> list[str]:
    """등록된 루프 스펙 이름 목록(파일명 stem)."""
    base = Path(loops_dir) if loops_dir else _DEFAULT_LOOPS_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))
