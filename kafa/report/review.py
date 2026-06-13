"""Phase 4 — 검토 리포트 (담당자 전용).

한 화면 요약 + 체크포인트:
  - 미추천/미해소·검토 플래그(비영업용 승용차 의심, 개인 상대계정 폴백, 의제 후보 등)
  - Phase 2 추천 내역(근거·신뢰도)
  - 부가율 이상 거래처(표준 10% 편차 / 공급가액 없는데 세액)
  - 홈택스 미등록 의심 거래처(사업자번호 체크섬 무효·미상, 또는 마스터 미등록 신규)
  - 스킵(중복전표)

콘솔/외부 노출 요약은 security.mask_* 로 마스킹(기본 mask=True).
중간 산출물 CSV(write_review_csv)는 담당자 로컬 검증용 — 기본은 마스킹, raw 필요 시 mask=False.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from kafa.config_loader import load_rules
from kafa.rules.models import ClassifiedRow, Deduct, Verdict
from kafa.security import mask_bizno, mask_name
from kafa.validate import valid_bizno, vat_rate_anomaly

_DEDUCT_LABEL = {
    Deduct.DEDUCTIBLE: "공제",
    Deduct.NON_DEDUCTIBLE: "불공제",
    Deduct.REVIEW: "검토",
}


@dataclass
class ReviewReport:
    total: int = 0
    written: int = 0
    skipped: int = 0
    unresolved: int = 0
    needs_review: int = 0
    recommended: int = 0
    reversals: int = 0
    deemed_candidates: int = 0
    review_lines: list[str] = field(default_factory=list)
    recommendation_lines: list[str] = field(default_factory=list)
    vat_anomaly_lines: list[str] = field(default_factory=list)
    suspect_vendor_lines: list[str] = field(default_factory=list)


def _tag(row: ClassifiedRow, *, mask: bool) -> str:
    name = row.source.거래처 if row.source else ""
    bizno = row.source.사업자등록번호 if row.source else ""
    if mask:
        name, bizno = mask_name(name), mask_bizno(bizno)
    return f"[{name} {bizno}]".strip()


def build_review(
    rows: list[ClassifiedRow],
    *,
    mask: bool = True,
    known_biznos: Optional[Iterable[str]] = None,
    config_dir: str | None = None,
) -> ReviewReport:
    """분류 결과 목록 → 검토 리포트. rows 에는 스킵된 행이 포함될 수 있다."""
    rep_cfg = load_rules(config_dir).get("report", {}) or {}
    vat_tol = float(rep_cfg.get("vat_rate_tol", 0.01))
    known = {("".join(c for c in (b or "") if c.isdigit())) for b in (known_biznos or [])}

    rep = ReviewReport(total=len(rows))
    seen_vat: set[str] = set()
    seen_suspect: set[str] = set()

    for r in rows:
        if r.skipped:
            rep.skipped += 1
            continue
        rep.written += 1

        if r.판정유형 == Verdict.UNRESOLVED:
            rep.unresolved += 1
        if r.판정유형 == Verdict.RECOMMENDED:
            rep.recommended += 1
        if r.needs_review:
            rep.needs_review += 1
        if r.is_reversal:
            rep.reversals += 1
        if r.의제대상여부:
            rep.deemed_candidates += 1

        tag = _tag(r, mask=mask)
        src = r.source

        # 미해소/검토
        if r.needs_review or r.판정유형 == Verdict.UNRESOLVED:
            reasons = "; ".join(r.review_reasons) or r.판정유형.value
            rep.review_lines.append(f"{tag} {reasons}")

        # 추천 내역(근거·신뢰도)
        if r.판정유형 == Verdict.RECOMMENDED:
            conf = f"{r.신뢰도:.0%}" if r.신뢰도 is not None else "-"
            basis = r.추천근거 or "추천"
            rep.recommendation_lines.append(
                f"{tag} 차변 {r.차변계정코드} ← {basis} (신뢰도 {conf})")

        if src is None:
            continue

        # 부가율 이상(거래처 단위 dedup)
        anomaly = vat_rate_anomaly(src.공급가액, src.세액, tol=vat_tol)
        if anomaly and tag not in seen_vat:
            seen_vat.add(tag)
            rep.vat_anomaly_lines.append(f"{tag} {anomaly}")

        # 미등록 의심(체크섬 무효/미상, 또는 마스터 미등록 신규)
        bizno_digits = "".join(c for c in (src.사업자등록번호 or "") if c.isdigit())
        suspect_reason = None
        if not bizno_digits:
            suspect_reason = "사업자번호 미상"
        elif not valid_bizno(bizno_digits):
            suspect_reason = "사업자번호 체크섬 무효"
        elif known and bizno_digits not in known:
            suspect_reason = "마스터 미등록 신규 거래처"
        if suspect_reason and tag not in seen_suspect:
            seen_suspect.add(tag)
            rep.suspect_vendor_lines.append(f"{tag} {suspect_reason}")

    return rep


def _section(title: str, lines: list[str]) -> str:
    if not lines:
        return ""
    body = "\n".join(f"  - {ln}" for ln in lines)
    return f"\n● {title} ({len(lines)})\n{body}"


def render_report(rep: ReviewReport) -> str:
    """한 화면 요약 + 체크포인트."""
    head = (
        "── 검토 리포트 (담당자 전용) ──\n"
        f"총 {rep.total} | 작성 {rep.written} | 스킵 {rep.skipped} | "
        f"미해소 {rep.unresolved} | 추천 {rep.recommended} | 검토 {rep.needs_review} | "
        f"반전 {rep.reversals} | 의제후보 {rep.deemed_candidates}"
    )
    parts = [
        head,
        _section("미해소·검토(담당자 확인 필요)", rep.review_lines),
        _section("미추천 자동 추천 내역", rep.recommendation_lines),
        _section("부가율 이상 거래처", rep.vat_anomaly_lines),
        _section("홈택스 미등록 의심 거래처", rep.suspect_vendor_lines),
    ]
    out = "".join(p for p in parts if p)
    # 체크포인트
    checks = []
    if rep.unresolved:
        checks.append(f"미해소 {rep.unresolved}건 계정 확정")
    if rep.needs_review:
        checks.append(f"검토 {rep.needs_review}건 공제/계정 판단")
    if rep.vat_anomaly_lines:
        checks.append(f"부가율 이상 {len(rep.vat_anomaly_lines)}거래처 확인")
    if rep.suspect_vendor_lines:
        checks.append(f"미등록 의심 {len(rep.suspect_vendor_lines)}거래처 홈택스 조회")
    if checks:
        out += "\n\n☑ 체크포인트: " + " · ".join(checks)
    return out


# 중간 산출물 CSV 컬럼 (담당자 검증용 — 수작업 결과와 대조)
_CSV_COLUMNS = [
    "거래일자", "거래처", "사업자번호", "품명", "공급가액", "세액", "합계",
    "유형코드", "차변계정코드", "대변계정코드", "공제여부",
    "의제대상여부", "면세매입액", "판정유형", "신뢰도", "추천근거",
    "검토사유", "판정근거", "스킵사유",
]


def write_review_csv(rows: list[ClassifiedRow], path: str | Path,
                     *, mask: bool = True) -> Path:
    """분류 결과 중간 산출물을 CSV로 (담당자 전용, 로컬). 기본 마스킹.

    수용 기준의 '수작업 결과 대조'·'추천+근거+신뢰도' 검증에 사용.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_CSV_COLUMNS)
        for r in rows:
            src = r.source
            name = src.거래처 if src else ""
            bizno = src.사업자등록번호 if src else ""
            if mask:
                name, bizno = mask_name(name), mask_bizno(bizno)
            w.writerow([
                f"{src.연도}-{src.일자}".strip("-") if src else "",
                name, bizno,
                src.품명 if src else "",
                src.공급가액 if src else "",
                src.세액 if src else "",
                src.합계 if src else "",
                r.유형코드 if r.유형코드 is not None else "",
                r.차변계정코드 if r.차변계정코드 is not None else "",
                r.대변계정코드 if r.대변계정코드 is not None else "",
                _DEDUCT_LABEL.get(r.공제여부, ""),
                "Y" if r.의제대상여부 else "",
                r.면세매입액 or "",
                r.판정유형.value,
                f"{r.신뢰도:.2f}" if r.신뢰도 is not None else "",
                r.추천근거,
                "; ".join(r.review_reasons),
                ",".join(r.판정근거),
                r.skip_reason,
            ])
    return out


# 하위호환: 간이 요약 (기존 호출부 유지)
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
    rep = build_review(rows, mask=mask)
    return ReviewSummary(
        total=rep.total, skipped=rep.skipped, unresolved=rep.unresolved,
        needs_review=rep.needs_review, reversals=rep.reversals,
        deemed_candidates=rep.deemed_candidates, lines=rep.review_lines,
    )


def render_text(summary: ReviewSummary) -> str:
    head = (f"총 {summary.total}건 | 스킵 {summary.skipped} | "
            f"미해소 {summary.unresolved} | 검토 {summary.needs_review} | "
            f"반전 {summary.reversals} | 의제후보 {summary.deemed_candidates}")
    body = "\n".join(f"  - {ln}" for ln in summary.lines)
    return head + ("\n" + body if body else "")
