"""세무대리인 보조 모듈(kafa/agent) 단위테스트 — 합성·비-PII 데이터만."""
from decimal import Decimal

import pytest

from kafa.agent.bizno_batch import check_biznos, render_bizno_summary
from kafa.agent.intake import (build_intake_checklist, draft_request_message,
                               render_intake_checklist)
from kafa.agent.prefile_check import build_prefile_check, render_prefile_check
from kafa.agent.recon import VendorBaseline, reconcile, render_recon
from kafa.agent.withholding import compute_batch, compute_withholding, render_withholding
from kafa.rules.models import ClassifiedRow, Deduct, InputRow

# 체크섬 유효/무효 사업자번호(합성)
VALID_BIZNO = "123-45-67891"
INVALID_BIZNO = "123-45-67890"


def _row(*, 공급가액="10000", 세액="1000", 비과세="0", 합계="11000",
         공제=Deduct.DEDUCTIBLE, bizno=VALID_BIZNO, 거래처="가맹점", code=811,
         skipped=False, skip_reason=""):
    src = InputRow(거래처=거래처, 공급가액=Decimal(공급가액), 세액=Decimal(세액),
                   비과세=Decimal(비과세), 합계=Decimal(합계), 사업자등록번호=bizno)
    return ClassifiedRow(차변계정코드=code, 공제여부=공제, source=src,
                         skipped=skipped, skip_reason=skip_reason)


# ── 초안1 신고 전 점검 ──

def test_prefile_all_ok():
    rep = build_prefile_check([_row(), _row()])
    assert rep.all_ok
    assert "신고 준비 완료" in render_prefile_check(rep)


def test_prefile_flags_problems():
    rows = [
        _row(합계="99999"),                       # 합계 불일치
        _row(bizno=INVALID_BIZNO),                # 체크섬 오류
        _row(공급가액="10000", 세액="5000"),       # 부가율 이상(50%)
        _row(공제=Deduct.REVIEW),                  # 검토 잔여
        _row(skipped=True, skip_reason="중복전표"),  # 중복(정보성)
    ]
    rep = build_prefile_check(rows)
    assert not rep.all_ok
    names = {w.name for w in rep.warnings}
    assert any("합계 검산" in n for n in names)
    assert any("체크섬" in n for n in names)
    assert any("부가율" in n for n in names)
    assert any("공제여부" in n for n in names)
    # 중복은 정보성(스킵 처리됨) → 경고 아님
    assert not any("중복" in n for n in names)


# ── 초안2 사업자번호 일괄검증 ──

def test_bizno_batch_dedupe_and_validate():
    res = check_biznos([VALID_BIZNO, VALID_BIZNO, INVALID_BIZNO, "", "abc"])
    assert res.total_input == 3          # 빈/비숫자 제외
    assert res.unique == 2 and res.duplicates == 1
    assert res.n_valid == 1 and res.n_invalid == 1
    # 조회 대상은 원문(로컬), 요약은 마스킹
    assert res.query_list == ["1234567891"]
    summary = render_bizno_summary(res)
    assert "123-**-*****" in summary and "1234567890" not in summary


# ── 초안3 원천징수 ──

def test_withholding_business_income():
    w = compute_withholding(1_000_000, "사업소득")
    assert w.소득세 == Decimal("30000") and w.지방소득세 == Decimal("3000")
    assert w.원천징수합계 == Decimal("33000") and w.실지급액 == Decimal("967000")
    assert not w.소액부징수_검토


def test_withholding_other_income_and_small_amount():
    w = compute_withholding(1_000_000, "기타소득")
    assert w.소득세 == Decimal("80000") and w.원천징수합계 == Decimal("88000")
    small = compute_withholding(20_000, "사업소득")   # 소득세 600 < 1000
    assert small.소액부징수_검토


def test_withholding_unknown_type_raises():
    with pytest.raises(ValueError):
        compute_withholding(1000, "근로소득")


def test_withholding_batch_render():
    batch = compute_batch([(1_000_000, "사업소득"), (500_000, "기타소득")])
    assert batch.지급액합계 == Decimal("1500000")
    assert batch.원천징수합계 == Decimal("33000") + Decimal("44000")
    assert "원천징수 총액" in render_withholding(batch)


# ── 초안4 자료 수취 ──

def test_intake_missing_and_request_message():
    c = build_intake_checklist("2026-03", "행복상사",
                               received_types=["신용카드 매입내역", "통장 거래내역"])
    assert not c.complete and c.client == "행복**"   # 마스킹
    assert "매입 세금계산서" in c.missing
    txt = render_intake_checklist(c)
    assert "[v] 신용카드 매입내역" in txt and "← 미수취" in txt
    msg = draft_request_message(c)
    assert "행복**" in msg and "매입 세금계산서" in msg


def test_intake_complete():
    from kafa.config_loader import load_agent
    all_types = load_agent()["intake"]["자료유형"]
    c = build_intake_checklist("2026-03", "ㄱ", received_types=all_types)
    assert c.complete
    assert "모두 수취 완료" in draft_request_message(c)


# ── 초안5 거래처/계정 대사 ──

def test_recon_new_and_changed(tmp_path):
    # 1차: 기준선 비어있음 → 모두 신규
    rows1 = [_row(거래처="에이상사", code=811), _row(거래처="비비유통", code=812)]
    res1, base1 = reconcile(rows1, {})
    assert len(res1.new_vendors) == 2 and not res1.account_changes

    # 2차: 에이상사 계정 변경, 신규 씨씨물산
    rows2 = [_row(거래처="에이상사", code=999),   # 계정 변동
             _row(거래처="씨씨물산", code=813)]    # 신규
    res2, base2 = reconcile(rows2, base1)
    assert res2.new_vendors == ["씨씨**"]
    assert res2.account_changes and res2.account_changes[0][1:] == (811, 999)
    assert "계정 변동" in render_recon(res2)


def test_vendor_baseline_roundtrip(tmp_path):
    p = tmp_path / "base.json"
    vb = VendorBaseline(p)
    vb.save({"abc123": 811})
    assert VendorBaseline(p).mapping == {"abc123": 811}
