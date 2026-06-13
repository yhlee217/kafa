"""Phase 4 (골격) — 검토 리포트 요약.

담당자 전용. 한 화면 요약 + 체크포인트:
  - 미추천/미해소 행
  - 검토 플래그(비영업용 승용차 의심, 개인 상대계정 폴백, 의제 후보 등)
  - 스킵(중복전표) 목록
  - [TODO] 부가율 이상 거래처, 홈택스 미등록 의심 신규 거래처
리포트에 거래처 실명/사업자번호를 노출할 때는 security.mask_* 사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from kafa.rules.models import ClassifiedRow, Verdict
from kafa.security import mask_bizno, mask_name


@dataclass
class ReviewSummary:
    total: int = 0
    skipped: int = 0
    unresolved: int = 0
    needs_review: int = 0
    reversals: int = 0
    deemed_candidates: int = 0
    lines: list[str] = field(default_factory=list)


def build_summary(rows: list[ClassifiedRow], *, mask: bool = True) -> ReviewSummary:
    s = ReviewSummary(total=len(rows))
    for r in rows:
        if r.skipped:
            s.skipped += 1
            continue
        if r.판정유형 == Verdict.UNRESOLVED:
            s.unresolved += 1
        if r.needs_review:
            s.needs_review += 1
        if r.is_reversal:
            s.reversals += 1
        if r.의제대상여부:
            s.deemed_candidates += 1
        if r.needs_review or r.판정유형 == Verdict.UNRESOLVED:
            name = r.source.거래처 if r.source else ""
            bizno = r.source.사업자등록번호 if r.source else ""
            if mask:
                name, bizno = mask_name(name), mask_bizno(bizno)
            reasons = "; ".join(r.review_reasons) or r.판정유형.value
            s.lines.append(f"[{name} {bizno}] {reasons}")
    return s


def render_text(summary: ReviewSummary) -> str:
    head = (f"총 {summary.total}건 | 스킵 {summary.skipped} | "
            f"미해소 {summary.unresolved} | 검토 {summary.needs_review} | "
            f"반전 {summary.reversals} | 의제후보 {summary.deemed_candidates}")
    body = "\n".join(f"  - {ln}" for ln in summary.lines)
    return head + ("\n" + body if body else "")
