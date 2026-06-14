"""고객 제공용 요약 리포트 (세무대리인 → 고객).

세무대리인이 고객(사업주)에게 건네는 비전문가용 한국어 요약. 처리 현황 + 부가세 관점 +
**고객이 직접 확인해야 할 항목**(용도/적격증빙 확인)을 안내한다.

식별은 비-PII(거래일자·품명/업태·금액)로 하고 거래처명은 마스킹(기본) — 고객은 날짜·금액으로
자기 거래를 알아본다. 안전(LLM/외부 노출에도 PII 없음) + 실사용(확인 가능) 둘 다 만족.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from kafa.report.vat_summary import build_vat_summary
from kafa.rules.models import ClassifiedRow, Verdict
from kafa.security import mask_name
from kafa.validate import valid_bizno, vat_rate_anomaly


@dataclass
class AttentionItem:
    date: str
    desc: str          # 품명/업태 (비-PII)
    amount: Decimal
    vendor_masked: str
    reason: str        # 고객 언어로 된 확인 요청


def _client_reason(r: ClassifiedRow) -> str | None:
    """이 행이 고객 확인이 필요한 이유(고객 언어). 없으면 None."""
    rs = " ".join(r.review_reasons)
    src = r.source
    bizno = "".join(c for c in (src.사업자등록번호 if src else "") if c.isdigit())

    if "비영업용" in rs or "승용차" in rs:
        return "업무용(영업용) 차량 관련 지출이 맞는지 확인 부탁드립니다."
    if "접대" in rs:
        return "접대성 지출인지(거래처 접대 등) 확인 부탁드립니다."
    if "사업무관" in rs:
        return "사업과 관련된 지출이 맞는지 확인 부탁드립니다."
    if (r.판정유형 == Verdict.UNRESOLVED and r.차변계정코드 is None):
        return "어떤 용도의 지출인지 알려주시면 계정을 확정하겠습니다."
    if bizno and not valid_bizno(bizno):
        return "거래처 사업자번호가 확인되지 않습니다 — 영수증/전자증빙을 확인 부탁드립니다."
    if src and vat_rate_anomaly(src.공급가액, src.세액):
        return "부가세액이 일반적이지 않아 영수증 확인이 필요합니다."
    if r.needs_review and rs:
        return f"확인 부탁드립니다({rs})."
    return None


def build_client_report(rows: list[ClassifiedRow], *,
                        period_label: str = "이번 기간",
                        mask: bool = True,
                        config_dir: str | None = None) -> str:
    """고객 제공용 한국어 요약 텍스트."""
    written = [r for r in rows if not r.skipped]
    total_amount = sum((Decimal(r.source.합계 or 0) for r in written if r.source), Decimal(0))
    vat = build_vat_summary(rows)

    attention: list[AttentionItem] = []
    for r in written:
        reason = _client_reason(r)
        if reason and r.source is not None:
            desc = (r.source.품명 or r.source.업태 or "").strip() or "(품목 미상)"
            attention.append(AttentionItem(
                date=f"{r.source.연도}-{r.source.일자}".strip("-"),
                desc=desc,
                amount=Decimal(r.source.합계 or 0),
                vendor_masked=mask_name(r.source.거래처) if mask else (r.source.거래처 or ""),
                reason=reason,
            ))

    lines = [
        f"[{period_label}] 신용카드 매입 처리 안내",
        "",
        f"안녕하세요. {period_label} 신용카드 매입 자료를 정리해 안내드립니다.",
        "",
        "■ 처리 현황",
        f"  - 매입 건수: 총 {len(written)}건",
        f"  - 매입 금액(합계): {total_amount:,}원",
        "",
        "■ 부가세 관점 (참고 — 최종 세액은 신고 시 확정)",
        f"  - 공제 대상 매입: {vat.과세공제_공급가액:,}원 (관련 부가세 약 {vat.공제_매입세액:,}원)",
        f"  - 불공제 매입: {vat.불공제_공급가액:,}원",
        f"  - 면세 매입: {vat.면세_금액:,}원",
    ]
    if vat.의제대상_건수:
        lines.append(f"  - 의제매입 대상(음식점 등) 후보: {vat.의제대상_면세매입액:,}원 "
                     f"(공제 가능 여부는 신고 시 검토)")

    lines += ["", "■ 고객님 확인 요청 사항"]
    if attention:
        lines.append("  아래 거래의 용도/증빙을 확인해 주시면 정확히 반영하겠습니다(날짜·금액으로 확인).")
        for a in attention:
            lines.append(f"  • {a.date} · {a.desc} · {a.amount:,}원 [{a.vendor_masked}]")
            lines.append(f"      → {a.reason}")
    else:
        lines.append("  특별히 확인이 필요한 항목은 없습니다. 그대로 반영하겠습니다.")

    lines += ["", "문의사항이 있으시면 회신 부탁드립니다. 감사합니다."]
    return "\n".join(lines)
