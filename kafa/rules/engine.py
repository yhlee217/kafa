"""룰 엔진 오케스트레이션: InputRow → ClassifiedRow.

순수 함수들을 spec 파이프라인 순서로 묶는다:
  skip(1.8) → deductibility(1.3) → vat_type(1.1) → account(1.7)
  → counterparty(1.2) → deemed_credit(1.6) → negative(1.9) → vendor_match.

핵심 가치(spec): 위하고가 채운 행은 '매핑만', 차변계정 미정(미추천) 행은
Phase 2 추천 대상으로 unresolved 표시한다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from kafa.config_loader import load_rules
from kafa.rules.accounts import map_account_name_to_code
from kafa.rules.agents import agent_note, agent_of
from kafa.rules.counterparty import resolve_counterparty
from kafa.rules.deductibility import resolve_deductibility
from kafa.rules.deemed_credit import deemed_credit_flag
from kafa.rules.models import ClassifiedRow, Deduct, InputRow, Verdict
from kafa.rules.negative import apply_reversal, is_negative
from kafa.rules.vat_type import derive_vat_type, label_to_code, map_prefilled
from kafa.rules.vendor_match import VendorMaster, match_vendor

RULE_SKIP = "SKIP-001"
RULE_ACC_PENDING = "ACC-PENDING"   # 미추천 → Phase 2 추천 대상
RULE_AGENT = "AGENT-001"           # 거래처가 결제대행사 → 담당자 확인
CODE_카면 = 58


def _infer_taxation(row: InputRow, *, config_dir: str | None) -> str:
    """과세/면세 추정. 위하고 유형 라벨 우선(카면→면세), 없으면 과세 기본."""
    code = label_to_code(row.유형, config_dir=config_dir)
    if code == CODE_카면:
        return "면세"
    return "과세"


def classify_row(
    row: InputRow,
    *,
    client_type: Optional[str] = None,
    master: Optional[VendorMaster] = None,
    config_dir: str | None = None,
) -> ClassifiedRow:
    cfg = load_rules(config_dir)
    out = ClassifiedRow(source=row)

    # 1.8 중복전표 등 스킵
    if row.전표상태.strip() in set(cfg.get("skip_status", []) or []):
        out.skipped = True
        out.skip_reason = row.전표상태.strip()
        out.add_rule(RULE_SKIP)
        return out

    # 1.3 공제여부 (3단)
    ded = resolve_deductibility(row.국세청, row.업태, row.종목, row.품명,
                                봉사료=row.비과세, config_dir=config_dir)
    out.공제여부 = ded.value
    out.add_rule(ded.rule_id)
    if ded.verdict == Verdict.REVIEW:
        out.needs_review = True
        out.review_reasons.append(ded.note or "공제여부 검토")

    taxation = _infer_taxation(row, config_dir=config_dir)
    prefilled_code = label_to_code(row.유형, config_dir=config_dir)

    # 1.1 유형코드
    if ded.value == Deduct.NON_DEDUCTIBLE:
        # 불공제 확정 → 유형 일반(3)으로 오버라이드 (전용 코드 없음)
        vt = derive_vat_type(taxation, Deduct.NON_DEDUCTIBLE, config_dir=config_dir)
    elif row.유형.strip() and prefilled_code is not None:
        # 위하고가 채운 행 → 매핑만
        vt = map_prefilled(row.유형, config_dir=config_dir)
    else:
        # 유형 미정/미상 → 도출(검토는 공제 경로로 도출, 확정은 담당자)
        eff = Deduct.DEDUCTIBLE if ded.value == Deduct.REVIEW else ded.value
        vt = derive_vat_type(taxation, eff, config_dir=config_dir)
    out.유형코드 = vt.value
    out.add_rule(vt.rule_id)
    if vt.verdict == Verdict.UNRESOLVED:
        out.판정유형 = Verdict.UNRESOLVED

    # 1.7 차변계정코드. 미추천(차변 비어있음)이면 Phase 2 추천 대상 → unresolved.
    if row.차변계정.strip():
        acc = map_account_name_to_code(row.차변계정, config_dir=config_dir)
        out.차변계정코드 = acc.value
        out.add_rule(acc.rule_id)
        if acc.verdict == Verdict.UNRESOLVED:
            out.판정유형 = Verdict.UNRESOLVED
            out.needs_review = True
            out.review_reasons.append(acc.note or "차변계정 미매핑")
    else:
        out.차변계정코드 = None
        out.판정유형 = Verdict.UNRESOLVED
        out.add_rule(RULE_ACC_PENDING)

    # 결제대행사 — 거래처가 실제 가맹점이 아니면 계정을 정할 근거가 없다.
    #  이름만으로 계정을 찍지 않고 담당자에게 넘긴다(자동 추천의 오답을 막는다).
    if (cfg.get("payment_agents") or {}).get("flag_review", True):
        found = agent_of(row.거래처, config_dir=config_dir)
        if found:
            group, label = found
            out.is_agent = True
            out.agent_group = group
            out.add_rule(RULE_AGENT)
            out.needs_review = True
            out.review_reasons.append(
                agent_note(group, label, config_dir=config_dir))

    # 1.2 대변(상대계정) — 기장 클라이언트 기준
    cp = resolve_counterparty(client_type, config_dir=config_dir)
    code, vendor = cp.value
    out.대변계정코드 = code
    out.대변거래처 = vendor
    out.add_rule(cp.rule_id)
    if cp.verdict == Verdict.REVIEW:
        out.needs_review = True
        out.review_reasons.append(cp.note or "상대계정 검토")

    # 1.6 의제매입 (플래그만). 면세매입액 = 카면일 때 공급가액.
    면세금액 = Decimal(row.공급가액) if taxation == "면세" else Decimal(0)
    dc = deemed_credit_flag(out.유형코드, 면세금액, config_dir=config_dir)
    의제, 면세매입 = dc.value
    out.의제대상여부 = bool(의제)
    out.면세매입액 = Decimal(면세매입)
    out.add_rule(dc.rule_id)
    if dc.verdict == Verdict.REVIEW:
        out.needs_review = True
        out.review_reasons.append(dc.note or "의제 검토")

    # 1.9 환불/취소(음수): 방향 반전. 차변 미정(미추천)이면 플래그만, 실제 swap은
    #     추천으로 차변이 채워진 뒤 finalize_reversal 에서 적용.
    if is_negative(row.합계):
        if out.차변계정코드 is not None and out.대변계정코드 is not None:
            apply_reversal(out)
        else:
            out.is_reversal = True

    # 거래처 ↔ 마스터 매칭 (정확→후보→미매칭). 마스터 있을 때만 의미.
    if master is not None:
        mr = match_vendor(row.거래처, row.사업자등록번호, master)
        out.add_rule(mr.rule_id)
        if mr.status == Verdict.REVIEW:
            out.needs_review = True
            out.review_reasons.append(f"거래처 후보 확인: {mr.candidate}")
        elif mr.status == Verdict.UNRESOLVED:
            out.review_reasons.append("거래처 마스터 미매칭")

    return out


def finalize_reversal(out: ClassifiedRow) -> ClassifiedRow:
    """미추천이라 미뤄둔 음수 건의 방향 반전을 차변 확정 후 적용."""
    if out.is_reversal and not out.reversal_applied:
        if out.차변계정코드 is not None and out.대변계정코드 is not None:
            apply_reversal(out)
    return out
