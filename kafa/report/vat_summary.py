"""부가세 신고 보조 집계 (세무대리인 고객 서비스).

분류 결과(ClassifiedRow) 위에서 신용카드 '매입'을 부가가치세 신고용으로 집계한다.
순수 합산만 한다 — 율/한도/세액 계산은 신고 단계로 넘긴다(보류 원칙 유지).
금액은 PII가 아니므로 합계는 노출 가능(거래처/사업자번호는 미포함).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from kafa.rules.models import ClassifiedRow, Deduct

CODE_면세 = 58


@dataclass
class VatSummary:
    written: int = 0
    # 과세 매입(공제 대상) — 공제 매입세액의 근거
    과세공제_건수: int = 0
    과세공제_공급가액: Decimal = Decimal(0)
    과세공제_세액: Decimal = Decimal(0)
    # 불공제 매입(매입세액 불공제)
    불공제_건수: int = 0
    불공제_공급가액: Decimal = Decimal(0)
    불공제_세액: Decimal = Decimal(0)
    # 면세 매입(카면)
    면세_건수: int = 0
    면세_금액: Decimal = Decimal(0)
    # 의제매입 대상 후보(플래그만 — 율/세액은 신고 단계)
    의제대상_건수: int = 0
    의제대상_면세매입액: Decimal = Decimal(0)
    # 담당자 확인 필요(공제여부 미확정 — 집계 잠정 제외 알림)
    검토_건수: int = 0
    검토_공급가액: Decimal = Decimal(0)
    검토_세액: Decimal = Decimal(0)

    @property
    def 공제_매입세액(self) -> Decimal:
        return self.과세공제_세액


def build_vat_summary(rows: list[ClassifiedRow]) -> VatSummary:
    s = VatSummary()
    for r in rows:
        if r.skipped or r.source is None:
            continue
        s.written += 1
        supply = Decimal(r.source.공급가액 or 0)
        tax = Decimal(r.source.세액 or 0)

        if r.공제여부 == Deduct.REVIEW:
            s.검토_건수 += 1
            s.검토_공급가액 += supply
            s.검토_세액 += tax
        elif r.공제여부 == Deduct.NON_DEDUCTIBLE:
            s.불공제_건수 += 1
            s.불공제_공급가액 += supply
            s.불공제_세액 += tax
        elif r.유형코드 == CODE_면세:
            s.면세_건수 += 1
            s.면세_금액 += supply
        else:  # 과세 공제(카과 등)
            s.과세공제_건수 += 1
            s.과세공제_공급가액 += supply
            s.과세공제_세액 += tax

        if r.의제대상여부:
            s.의제대상_건수 += 1
            s.의제대상_면세매입액 += Decimal(r.면세매입액 or 0)
    return s


def render_vat_summary(s: VatSummary) -> str:
    """세무대리인용 부가세 신고 보조 요약(한 화면). 율/세액 계산은 신고 단계."""
    lines = [
        "── 부가세 신고 보조 집계 (신용카드 매입) ──",
        f"대상 {s.written}건",
        f"[공제 대상] 과세매입 {s.과세공제_건수}건 / 공급가액 {s.과세공제_공급가액:,} / "
        f"공제매입세액 {s.공제_매입세액:,}",
        f"[불공제]    {s.불공제_건수}건 / 공급가액 {s.불공제_공급가액:,} / 세액 {s.불공제_세액:,}",
        f"[면세매입]  {s.면세_건수}건 / 금액 {s.면세_금액:,}",
        f"[의제대상]  {s.의제대상_건수}건 / 면세매입액 {s.의제대상_면세매입액:,} "
        f"(율·한도는 신고 단계)",
    ]
    if s.검토_건수:
        lines.append(f"[확인 필요] {s.검토_건수}건 / 공급가액 {s.검토_공급가액:,} "
                     f"— 공제여부 미확정, 담당자 확정 후 반영")
    return "\n".join(lines)
