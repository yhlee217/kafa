"""초안4 — 월별 자료 수취 체크리스트 + 누락 점검 + 요청 메시지 초안.

매달 고객에게 자료(카드내역·세금계산서·통장 등)를 요청하고 누락분을 독촉하던 반복
커뮤니케이션을, 고객별 월 체크리스트로 표준화한다. 자료유형은 config/agent.yaml 외부화.
고객명은 마스킹. (요청 메시지를 더 다듬고 싶으면 kafa.loop 의 guide 스펙으로 품질 루프 가능.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from kafa.config_loader import load_agent
from kafa.security import mask_name


@dataclass
class IntakeChecklist:
    period: str               # 예: "2026-03"
    client: str               # 마스킹된 고객명
    received: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


def build_intake_checklist(period: str, client_name: str,
                           received_types, *, mask: bool = True,
                           config_dir: str | None = None) -> IntakeChecklist:
    """기본 자료유형 대비 수취/누락 분류. received_types = 받은 자료유형 집합.

    mask=True(기본): 고객명 마스킹. mask=False: 자리표시자('{고객명}' 등)를 그대로 둘 때 사용.
    """
    types = load_agent(config_dir)["intake"]["자료유형"]
    recv = set(received_types or [])
    received = [t for t in types if t in recv]
    missing = [t for t in types if t not in recv]
    client = mask_name(client_name) if mask else client_name
    return IntakeChecklist(period=period, client=client,
                           received=received, missing=missing)


def render_intake_checklist(c: IntakeChecklist) -> str:
    lines = [f"── [{c.period}] {c.client} 자료 수취 체크리스트 ──"]
    for t in c.received:
        lines.append(f"  [v] {t}")
    for t in c.missing:
        lines.append(f"  [ ] {t}  ← 미수취")
    lines.append("수취 완료 ✅" if c.complete else f"미수취 {len(c.missing)}건 ⚠️")
    return "\n".join(lines)


def draft_request_message(c: IntakeChecklist) -> str:
    """고객에게 보낼 자료 요청 메시지 초안(누락분만). 담당자가 검토 후 발송."""
    if c.complete:
        return f"{c.client}님, {c.period} 자료 모두 수취 완료했습니다. 감사합니다."
    items = "\n".join(f"  - {t}" for t in c.missing)
    return (f"{c.client}님, {c.period} 기장 처리를 위해 아래 자료가 아직 도착하지 "
            f"않았습니다.\n{items}\n확인 후 회신 부탁드립니다.")
