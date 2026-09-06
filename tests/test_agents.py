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


def test_shipped_config_lists_every_group():
    from kafa.config_loader import load_rules
    groups = (load_rules().get("payment_agents") or {}).get("groups") or {}
    assert set(groups) == {"결제대행", "교통·선불정산", "주차·모빌리티",
                           "오픈마켓·배달", "카드사청구"}
    assert all(g.get("keywords") and g.get("note") for g in groups.values())


def test_shipped_config_covers_van_and_pg_alike():
    """카드 단말기(VAN)와 온라인 결제(PG)는 둘 다 실제 가맹점을 가린다."""
    for name in ("한국정보통신(주)", "나이스정보통신", "주식회사스마트로",
                 "（주）다우데이타", "한국결제네트웍스 유한회사", "（주）코밴",
                 "토스페이먼츠 주식회사", "네이버페이", "블루월넛 주식회사",
                 "웰컴페이먼츠 주식회사", "나이스페이먼츠 주식회사"):
        assert agent_of(name), name


def test_platform_and_parking_have_their_own_groups():
    assert agent_of("주식회사 우아한형제들")[0] == "오픈마켓·배달"
    assert agent_of("파킹클라우드 주식회사")[0] == "주차·모빌리티"


def test_parking_is_not_auto_confirmed():
    """주차는 여비교통비인지 차량유지비인지 갈리므로 자동 확정하지 않는다."""
    c = classify_row(_row(거래처="아이파킹", 차변계정=""), client_type="corporate")
    assert c.차변계정코드 is None and c.needs_review


# ── 종목 그물: 이름 목록에 없는 새 대행사도 잡는다 ──

def test_industry_catches_an_agent_missing_from_the_name_list():
    """대행사는 종목이 스스로를 말한다 — 이름을 몰라도 잡힌다."""
    from kafa.rules.agents import industry_agent_of

    assert agent_of("합성상호페이", 종목="전자지급결제대행업")[0] == "결제대행"
    assert industry_agent_of("통신업", "부가통신업")[0] == "결제대행"
    assert industry_agent_of("서비스", "전자금융업")[0] == "결제대행"


def test_industry_does_not_mistake_a_phone_bill_for_an_agent():
    """통신사도 종목에 '부가통신'이 붙지만 그건 통신비다."""
    from kafa.rules.agents import industry_agent_of

    assert industry_agent_of("통신업", "이동전화，무선호출，부가통신") is None
    assert industry_agent_of("전기통신업", "전신전화，통신설비임대, 부가통신") is None
    assert agent_of("합성통신사", 업태="통신업", 종목="이동전화，부가통신") is None


def test_industry_leaves_an_ordinary_merchant_alone():
    from kafa.rules.agents import industry_agent_of

    assert industry_agent_of("음식점업", "한식") is None
    assert industry_agent_of("", "") is None


def test_the_name_list_wins_over_the_industry_net():
    """이름이 정확하면 그 갈래를 쓴다 — 종목이 넓게 걸려도 밀리지 않는다."""
    group, label = agent_of("주식회사 티머니", 종목="전자화폐발행")
    assert group == "교통·선불정산" and label == "티머니"


def test_industry_hit_reports_the_matched_industry_not_the_vendor():
    """그물이 잡은 건 사유에 종목만 적는다(거래처 실명을 근거로 쓰지 않는다)."""
    group, label = agent_of("합성상호페이", 종목="전자지급결제대행업")
    assert "합성상호페이" not in label and label.startswith("종목")
    assert "합성상호페이" not in agent_note(group, label)


# ── 자동화: 성격이 분명한 갈래는 자동 확정, 나머지는 이력으로 ──

def test_transport_agent_is_auto_confirmed():
    """교통·선불정산은 성격이 분명해 기본 계정으로 자동 확정한다."""
    c = classify_row(_row(거래처="주식회사 티머니모빌리티", 차변계정=""),
                     client_type="corporate")
    assert c.차변계정코드 == 812                 # (판)여비교통비
    assert "AGENT-AUTO" in c.판정근거
    assert not c.needs_review                    # 사람이 볼 필요 없다
    assert "정산사업자" in c.추천근거


def test_auto_confirm_never_overwrites_a_filled_account():
    """위하고가 채운 값은 건드리지 않는다."""
    c = classify_row(_row(거래처="주식회사 티머니모빌리티",
                          차변계정="(판)복리후생비"), client_type="corporate")
    assert c.차변계정코드 == 811 and c.needs_review


def test_payment_agent_still_needs_review():
    """결제대행은 성격이 분명하지 않아 그대로 검토로 남는다."""
    c = classify_row(_row(거래처="토스페이먼츠 주식회사", 차변계정=""),
                     client_type="corporate")
    assert c.차변계정코드 is None and c.needs_review


def test_history_clears_the_agent_flag_when_confident():
    """그 수임처의 과거 처리가 일관되면 검토 플래그를 뗀다."""
    from kafa.cli import _settle_agent_review

    c = classify_row(_row(거래처="토스페이먼츠 주식회사", 차변계정=""),
                     client_type="corporate")
    assert c.needs_review
    _settle_agent_review(c, 0.95, None)
    assert not c.needs_review and "AGENT-HIST" in c.판정근거


def test_low_confidence_keeps_the_agent_flag():
    from kafa.cli import _settle_agent_review

    c = classify_row(_row(거래처="토스페이먼츠 주식회사", 차변계정=""),
                     client_type="corporate")
    _settle_agent_review(c, 0.5, None)
    assert c.needs_review


def test_other_review_reasons_survive():
    """대행사 사유만 떼고, 다른 검토 사유는 남긴다."""
    from kafa.cli import _settle_agent_review

    c = classify_row(_row(거래처="토스페이먼츠 주식회사", 차변계정="",
                          업태="주유소", 종목="경유"), client_type="corporate")
    before = len(c.review_reasons)
    _settle_agent_review(c, 0.95, None)
    assert c.needs_review and 0 < len(c.review_reasons) < before
