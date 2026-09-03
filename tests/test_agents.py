"""결제대행사 식별 — 거래처가 실제 가맹점이 아닌 경우. 합성 이름만 사용."""
from decimal import Decimal

from kafa.rules.agents import agent_note, agent_of, normalize
from kafa.rules.engine import classify_row
from kafa.rules.models import InputRow


def test_normalize_ignores_corporate_form_and_spacing():
    assert normalize("（주） 이비카드") == normalize("주식회사 이비카드")
    assert normalize("나이스정보통신(주)") == normalize("나이스정보통신（주）")
    assert normalize("") == ""


def test_finds_agents_by_group():
    assert agent_of("토스페이먼츠 주식회사")[0] == "결제대행"
    assert agent_of("（주） 이비카드")[0] == "교통·선불정산"
    assert agent_of("삼성카드주식회사")[0] == "카드사청구"


def test_ordinary_merchant_is_not_an_agent():
    assert agent_of("행복상사") is None
    assert agent_of("동네카페") is None


def test_note_explains_why_it_needs_checking():
    group, label = agent_of("한국정보통신（주）")
    note = agent_note(group, label)
    assert label in note and "실제 가맹점" in note


def _row(**kw):
    base = dict(연도="2026", 일자="03-15", 거래처="합성가맹점",
                공급가액=Decimal("10000"), 세액=Decimal("1000"),
                합계=Decimal("11000"), 국세청="공제", 유형="카과",
                차변계정="(판)복리후생비", 전표상태="", 사업자등록번호="000-00-00000")
    base.update(kw)
    return InputRow(**base)


def test_agent_row_is_flagged_for_review():
    c = classify_row(_row(거래처="토스페이먼츠 주식회사"), client_type="corporate")
    assert c.is_agent and c.agent_group == "결제대행"
    assert c.needs_review
    assert any("실제 가맹점" in r for r in c.review_reasons)
    assert "AGENT-001" in c.판정근거


def test_agent_flag_does_not_change_the_account():
    """계정을 바꾸지는 않는다 — 위하고가 채운 값은 그대로 두고 확인만 요청."""
    c = classify_row(_row(거래처="주식회사 티머니"), client_type="corporate")
    assert c.차변계정코드 == 811 and c.is_agent


def test_plain_row_keeps_agent_fields_empty():
    c = classify_row(_row(), client_type="corporate")
    assert not c.is_agent and c.agent_group == ""


def test_shipped_config_lists_the_three_groups():
    from kafa.config_loader import load_rules
    groups = (load_rules().get("payment_agents") or {}).get("groups") or {}
    assert set(groups) == {"결제대행", "교통·선불정산", "카드사청구"}
    assert all(g.get("keywords") and g.get("note") for g in groups.values())
