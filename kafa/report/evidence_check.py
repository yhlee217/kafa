"""증빙·리스크 점검 (세무대리인 서비스).

신용카드매출전표는 적격증빙(매입세액공제 가능)이다. 이 모듈은 "그럼에도 공제에서 빠지거나
확인이 필요한 건"을 거래별로 모아 세무대리인이 점검하게 한다:
  - 불공제(면세/비영업용 등)        ← 적격증빙이어도 공제 불가
  - 공제여부 미확정(검토)
  - 미등록/가공 의심(사업자번호 체크섬)
  - 부가율 이상
  - 중복전표(스킵)
검증 로직은 validate.py 를 재사용한다. 거래처는 마스킹, 날짜·품명·금액으로 식별.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal  # noqa: F401 — RiskFlag.amount 타입힌트(annotations)용

from kafa.report._common import row_identifier
from kafa.rules.models import ClassifiedRow, Deduct
from kafa.validate import valid_bizno, vat_rate_anomaly

CAT_불공제 = "불공제(공제 제외)"
CAT_검토 = "공제여부 확인 필요"
CAT_미등록 = "미등록/가공 의심"
CAT_부가율 = "부가율 이상"
CAT_중복 = "중복전표(제외됨)"


@dataclass
class RiskFlag:
    category: str
    date: str
    desc: str
    amount: Decimal
    vendor: str
    detail: str


@dataclass
class EvidenceReport:
    written: int = 0
    적격증빙_건수: int = 0          # 신용카드매출전표 = 적격
    공제가능_건수: int = 0          # 적격 & 공제
    flags: list[RiskFlag] = field(default_factory=list)

    def by_category(self) -> dict[str, list[RiskFlag]]:
        out: dict[str, list[RiskFlag]] = {}
        for f in self.flags:
            out.setdefault(f.category, []).append(f)
        return out


def build_evidence_check(rows: list[ClassifiedRow], *, mask: bool = True) -> EvidenceReport:
    rep = EvidenceReport()
    for r in rows:
        s = r.source
        date, desc, amount, vendor = row_identifier(r, mask=mask)

        if r.skipped:
            if (r.skip_reason or "").strip() == "중복전표":
                rep.flags.append(RiskFlag(CAT_중복, date, desc, amount, vendor,
                                          "위하고 중복전표 — 재반영 방지로 제외"))
            continue

        rep.written += 1
        rep.적격증빙_건수 += 1   # 신용카드 매입 = 적격증빙

        # 공제 여부
        if r.공제여부 == Deduct.DEDUCTIBLE:
            rep.공제가능_건수 += 1
        elif r.공제여부 == Deduct.NON_DEDUCTIBLE:
            rep.flags.append(RiskFlag(CAT_불공제, date, desc, amount, vendor,
                                      "; ".join(r.review_reasons) or "매입세액 불공제"))
        elif r.공제여부 == Deduct.REVIEW:
            rep.flags.append(RiskFlag(CAT_검토, date, desc, amount, vendor,
                                      "; ".join(r.review_reasons) or "공제여부 미확정"))

        if s is not None:
            bizno = "".join(c for c in (s.사업자등록번호 or "") if c.isdigit())
            if not bizno:
                rep.flags.append(RiskFlag(CAT_미등록, date, desc, amount, vendor,
                                          "사업자번호 미상"))
            elif not valid_bizno(bizno):
                rep.flags.append(RiskFlag(CAT_미등록, date, desc, amount, vendor,
                                          "사업자번호 체크섬 무효"))
            anomaly = vat_rate_anomaly(s.공급가액, s.세액)
            if anomaly:
                rep.flags.append(RiskFlag(CAT_부가율, date, desc, amount, vendor, anomaly))
    return rep


def render_evidence_check(rep: EvidenceReport) -> str:
    lines = [
        "── 증빙·리스크 점검 (신용카드 매입) ──",
        f"신용카드매출전표 = 적격증빙(매입세액공제 가능). 적격 {rep.적격증빙_건수}건 / "
        f"공제 가능 {rep.공제가능_건수}건.",
        f"점검 필요 {len(rep.flags)}건:",
    ]
    cats = rep.by_category()
    if not rep.flags:
        lines.append("  - 점검 필요 항목 없음.")
    for cat in (CAT_불공제, CAT_검토, CAT_미등록, CAT_부가율, CAT_중복):
        items = cats.get(cat)
        if not items:
            continue
        lines.append(f"\n● {cat} ({len(items)})")
        for f in items:
            lines.append(f"  • {f.date} · {f.desc} · {f.amount:,}원 [{f.vendor}] — {f.detail}")
    return "\n".join(lines)
