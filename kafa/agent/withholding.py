"""초안3 — 원천징수세액 계산 보조.

프리랜서·기타소득 지급 시 건건이 계산하던 원천징수세액(소득세 + 지방소득세)을 유형별로
자동 계산한다. 율은 config/agent.yaml 외부화. 원 단위 미만 절사.
보류: 비거주자·감면·필요경비율 변동·4대보험·소액부징수 적용범위는 담당자 확인(플래그만 제시).
지급받는 자의 성명·주민번호는 PII — 이 모듈은 금액만 처리한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from kafa.config_loader import load_agent


def _floor_won(x: Decimal) -> Decimal:
    """원 단위 미만 절사."""
    return x.quantize(Decimal("1"), rounding=ROUND_DOWN)


@dataclass
class Withholding:
    income_type: str
    지급액: Decimal
    소득세: Decimal
    지방소득세: Decimal
    원천징수합계: Decimal
    실지급액: Decimal
    소액부징수_검토: bool       # 소득세가 기준 미만 → 징수 제외 가능성(담당자 확인)
    note: str = ""


def compute_withholding(지급액, income_type: str, *,
                        config_dir: str | None = None) -> Withholding:
    """단건 원천징수 계산. income_type 은 agent.yaml 의 유형 키(사업소득/기타소득…)."""
    cfg = load_agent(config_dir)["withholding"]
    types = cfg["유형"]
    if income_type not in types:
        raise ValueError(
            f"미정의 원천징수 유형: {income_type!r} (가능: {list(types)})")
    base = Decimal(str(지급액))
    rate = Decimal(str(types[income_type]["소득세율"]))
    소득세 = _floor_won(base * rate)
    지방 = _floor_won(소득세 * Decimal(str(cfg["지방소득세_소득세대비율"])))
    합계 = 소득세 + 지방
    소액 = 소득세 < Decimal(str(cfg["소액부징수_기준"]))
    return Withholding(income_type=income_type, 지급액=base, 소득세=소득세,
                       지방소득세=지방, 원천징수합계=합계, 실지급액=base - 합계,
                       소액부징수_검토=소액, note=types[income_type].get("설명", ""))


@dataclass
class WithholdingBatch:
    rows: list[Withholding]

    @property
    def 지급액합계(self) -> Decimal:
        return sum((r.지급액 for r in self.rows), Decimal(0))

    @property
    def 원천징수합계(self) -> Decimal:
        return sum((r.원천징수합계 for r in self.rows), Decimal(0))

    @property
    def 실지급합계(self) -> Decimal:
        return sum((r.실지급액 for r in self.rows), Decimal(0))


def compute_batch(payments: list[tuple], *,
                  config_dir: str | None = None) -> WithholdingBatch:
    """payments = [(지급액, income_type), ...] → 일괄 계산표."""
    return WithholdingBatch([compute_withholding(a, t, config_dir=config_dir)
                             for a, t in payments])


def render_withholding(batch: WithholdingBatch) -> str:
    """계산표 텍스트(금액만 — 지급처 PII 미포함)."""
    lines = ["── 원천징수세액 계산 ──",
             f"{'유형':<8}{'지급액':>14}{'소득세':>12}{'지방세':>10}{'실지급':>14}"]
    for r in batch.rows:
        flag = " (소액부징수 검토)" if r.소액부징수_검토 else ""
        lines.append(f"{r.income_type:<8}{int(r.지급액):>14,}{int(r.소득세):>12,}"
                     f"{int(r.지방소득세):>10,}{int(r.실지급액):>14,}{flag}")
    lines.append(f"{'합계':<8}{int(batch.지급액합계):>14,}"
                 f"{'':>12}{'':>10}{int(batch.실지급합계):>14,}")
    lines.append(f"원천징수 총액: {int(batch.원천징수합계):,}원")
    return "\n".join(lines)
