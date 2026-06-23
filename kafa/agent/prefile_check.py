"""초안1 — 부가세 신고 전 자가검증 체크리스트.

신고 직전 담당자가 수기로 하던 사전 점검(합계 검산·공제 정합·사업자번호 오류·부가율
이상·중복)을 한 화면 PASS/WARN 으로 자동화한다.

중복 방지: 불공제/검토/미등록(체크섬·미상)/부가율/중복 집계는 이미 만든 EvidenceReport
(report.evidence_check)를 재사용한다. 이 모듈의 고유 검산은 **합계 검산**(공급가액+세액+
비과세=합계) 하나뿐이다. 출력은 건수(집계)만 — PII 미노출.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from kafa.report.evidence_check import (
    CAT_검토,
    CAT_미등록,
    CAT_부가율,
    CAT_불공제,
    CAT_중복,
    EvidenceReport,
    build_evidence_check,
)
from kafa.rules.models import ClassifiedRow


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


def _sum_mismatch(rows: list[ClassifiedRow], tol: Decimal) -> int:
    """합계 검산 — EvidenceReport 에 없는 유일 항목. 스킵 행 제외, tol 허용."""
    bad = 0
    for r in rows:
        if r.skipped or r.source is None:
            continue
        s = r.source
        diff = Decimal(s.공급가액) + Decimal(s.세액) + Decimal(s.비과세) - Decimal(s.합계)
        if abs(diff) > tol:
            bad += 1
    return bad


def build_prefile_check(rows: list[ClassifiedRow], *, tol: float = 0.01,
                        evidence: Optional[EvidenceReport] = None) -> PrefileReport:
    """신고 전 점검표 생성. evidence 를 주면 재사용(중복 스캔 방지), 없으면 새로 만든다."""
    ev = evidence if evidence is not None else build_evidence_check(rows)
    cats = ev.by_category()

    def n(cat: str) -> int:
        return len(cats.get(cat, []))

    mismatch = _sum_mismatch(rows, Decimal(str(tol)))
    불공제, 검토, 공제 = n(CAT_불공제), n(CAT_검토), ev.공제가능_건수
    bad_bizno, anomaly, dup = n(CAT_미등록), n(CAT_부가율), n(CAT_중복)

    items = [
        CheckItem("합계 검산(공급가액+세액+비과세=합계)", mismatch == 0,
                  f"불일치 {mismatch}건"),
        CheckItem("공제여부 분류 완료(검토 잔여 없음)", 검토 == 0,
                  f"공제 {공제} / 불공제 {불공제} / 검토 {검토}"),
        CheckItem("사업자번호 유효성(체크섬/미상)", bad_bizno == 0,
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
