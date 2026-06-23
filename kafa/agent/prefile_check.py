"""초안1 — 부가세 신고 전 자가검증 체크리스트.

신고 직전 담당자가 수기로 하던 사전 점검(합계 검산·공제 정합·사업자번호 오류·부가율
이상·중복)을 분류 결과로부터 한 화면 PASS/WARN 으로 자동화한다. 기존 validate.py 재사용.
출력은 건수(집계)만 — PII 미노출.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kafa.rules.models import ClassifiedRow, Deduct
from kafa.validate import valid_bizno, vat_rate_anomaly


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str


@dataclass
class PrefileReport:
    items: list[CheckItem]

    @property
    def warnings(self) -> list[CheckItem]:
        return [i for i in self.items if not i.ok]

    @property
    def all_ok(self) -> bool:
        return all(i.ok for i in self.items)


def build_prefile_check(rows: list[ClassifiedRow], *, tol: float = 0.01) -> PrefileReport:
    """분류 결과로 신고 전 점검표 생성. 스킵(중복 등) 행은 검산 대상에서 제외."""
    active = [r for r in rows if not r.skipped]

    # 1) 합계 검산: 공급가액 + 세액 + 비과세 == 합계 (엑셀 float 유래 오차 대비 tol 허용)
    tol_dec = Decimal(str(tol))
    mismatch = 0
    for r in active:
        s = r.source
        if s is None:
            continue
        diff = Decimal(s.공급가액) + Decimal(s.세액) + Decimal(s.비과세) - Decimal(s.합계)
        if abs(diff) > tol_dec:
            mismatch += 1

    # 2) 공제여부 분류 정합(검토 잔여가 있으면 신고 미완)
    공제 = sum(1 for r in active if r.공제여부 == Deduct.DEDUCTIBLE)
    불공제 = sum(1 for r in active if r.공제여부 == Deduct.NON_DEDUCTIBLE)
    검토 = sum(1 for r in active if r.공제여부 == Deduct.REVIEW)

    # 3) 사업자번호 체크섬(값이 있는데 무효)
    bad_bizno = sum(
        1 for r in active
        if r.source and r.source.사업자등록번호
        and not valid_bizno(r.source.사업자등록번호))

    # 4) 부가율 이상
    anomaly = sum(
        1 for r in active
        if r.source and vat_rate_anomaly(r.source.공급가액, r.source.세액, tol=tol))

    # 5) 중복 스킵(이미 제외됨 — 정보성)
    dup = sum(1 for r in rows
              if r.skipped and "중복" in (r.skip_reason or ""))

    items = [
        CheckItem("합계 검산(공급가액+세액+비과세=합계)", mismatch == 0,
                  f"불일치 {mismatch}건"),
        CheckItem("공제여부 분류 완료(검토 잔여 없음)", 검토 == 0,
                  f"공제 {공제} / 불공제 {불공제} / 검토 {검토}"),
        CheckItem("사업자번호 체크섬", bad_bizno == 0,
                  f"오류 의심 {bad_bizno}건"),
        CheckItem("부가율 이상 없음", anomaly == 0,
                  f"이상 {anomaly}건"),
        CheckItem("중복전표 처리(스킵)", True,
                  f"스킵 {dup}건"),
    ]
    return PrefileReport(items)


def render_prefile_check(rep: PrefileReport) -> str:
    """체크리스트 텍스트(담당자용). 비-PII 집계만."""
    head = "── 부가세 신고 전 자가검증 ──"
    verdict = "신고 준비 완료 ✅" if rep.all_ok else f"확인 필요 ⚠️ ({len(rep.warnings)}건)"
    lines = [head, verdict, ""]
    for it in rep.items:
        mark = "✅" if it.ok else "⚠️"
        lines.append(f"  {mark} {it.name} — {it.detail}")
    return "\n".join(lines)
