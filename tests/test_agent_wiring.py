"""제품 결선 — service 파사드(원천징수·자료수취) + agent 산출물 노출 테스트."""
from kafa import service


def test_withholding_calc_facade():
    out = service.withholding_calc([{"amount": 1_000_000, "type": "사업소득"},
                                    [500_000, "기타소득"]])
    assert out["ok"]
    assert len(out["rows"]) == 2
    assert out["원천징수합계"] == "77000"   # 33,000 + 44,000
    assert "원천징수" in out["report"]


def test_withholding_calc_rejects_negative():
    out = service.withholding_calc([{"amount": -1, "type": "사업소득"}])
    assert not out["ok"] and "error" in out


def test_withholding_calc_unknown_type():
    out = service.withholding_calc([{"amount": 1000, "type": "근로소득"}])
    assert not out["ok"]


def test_intake_checklist_facade():
    out = service.intake_checklist("2026-03", ["신용카드 매입내역"])
    assert out["ok"] and not out["complete"]
    assert "매입 세금계산서" in out["missing"]
    # 고객명은 받지 않고 자리표시자만(PII 미입력)
    assert "{고객명}" in out["request_message"]
    assert "{고객명}" in out["checklist"]
