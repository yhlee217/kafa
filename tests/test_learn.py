"""처리 이력 기반 규칙 추정 — 보류 항목별 관찰·미매핑·업종 패턴·PII 차단."""
from decimal import Decimal

from kafa.learn.infer import infer_rules, propose_config, render_inference
from kafa.rules.models import InputRow


def _row(**kw):
    base = dict(연도="2026", 일자="03-15", 거래처="가맹점", 품명="품명",
                공급가액=Decimal("10000"), 세액=Decimal("1000"), 비과세=Decimal("0"),
                합계=Decimal("11000"), 국세청="공제", 업태="", 종목="", 유형="카과",
                차변계정="(판)복리후생비", 대변계정="미지급비용", 구분="법인",
                사업자등록번호="111-11-11119")
    base.update(kw)
    return InputRow(**base)


def test_learned_rows_and_coverage():
    rows = [_row(), _row(차변계정="")]      # 두 번째는 미추천(학습 제외)
    rep = infer_rules(rows)
    assert rep.total_rows == 2 and rep.learned_rows == 1
    assert rep.coverage == 0.5


# ── 보류1: 개인사업자 상대계정 ──

def test_counterparty_detects_individual_account():
    rows = [_row(대변계정="인출금") for _ in range(4)] + [_row()]
    obs = next(o for o in infer_rules(rows).observations if o.topic.startswith("상대계정"))
    assert "인출금" in obs.finding and obs.support == 5


def test_counterparty_corporate_only():
    obs = next(o for o in infer_rules([_row() for _ in range(3)]).observations
               if o.topic.startswith("상대계정"))
    assert "미지급비용" in obs.finding and obs.confidence == 1.0


# ── 보류2: 면세(카면) ──

def test_tax_free_pattern_with_restaurant():
    rows = [_row(유형="카면", 업태="음식점", 국세청="불공제",
                 차변계정="(판)복리후생비") for _ in range(3)]
    obs = next(o for o in infer_rules(rows).observations if "면세" in o.topic)
    assert obs.support == 3 and "음식점업 3건" in obs.finding


def test_tax_free_absent():
    obs = next(o for o in infer_rules([_row()]).observations if "면세" in o.topic)
    assert obs.support == 0 and "추정 불가" in obs.finding


# ── 보류3: 간이과세 식별 ──

def test_simplified_identifier_found_and_absent():
    found = next(o for o in infer_rules([_row(구분="간이과세자")]).observations
                 if "간이" in o.topic)
    assert found.confidence == 1.0 and "간이과세자" in found.finding

    absent = next(o for o in infer_rules([_row(구분="법인")]).observations
                  if "간이" in o.topic)
    assert absent.confidence == 0.0 and "구분할 수 없음" in absent.finding


# ── 보류4: 봉사료(비과세) ──

def test_service_charge_signal():
    rows = [_row(비과세=Decimal("500"), 업태="음식점") for _ in range(3)]
    obs = next(o for o in infer_rules(rows).observations if "봉사료" in o.topic)
    assert obs.support == 3 and "봉사료 성격일 가능성" in obs.finding


# ── 미매핑 계정 / 업종 패턴 ──

def test_unmapped_accounts_listed():
    rows = [_row(차변계정="(판)지급수수료") for _ in range(2)] + [_row()]
    rep = infer_rules(rows)
    names = dict(rep.unmapped_accounts)
    assert names.get("(판)지급수수료") == 2       # config 에 없는 계정
    assert "(판)복리후생비" not in names          # 매핑되는 계정은 제외


def test_industry_pattern_needs_support_and_ratio():
    # 5건 이상 + 80% 이상 편중이어야 채택
    weak = infer_rules([_row(업태="카페") for _ in range(3)])
    assert weak.industry_accounts == {}

    rows = [_row(업태="주유소", 차변계정="(판)차량유지비") for _ in range(5)]
    strong = infer_rules(rows)
    assert strong.industry_accounts["주유소"][0] == "(판)차량유지비"
    assert strong.industry_accounts["주유소"][1] == 5

    mixed = [_row(업태="잡화", 차변계정="(판)복리후생비") for _ in range(3)] + \
            [_row(업태="잡화", 차변계정="(판)차량유지비") for _ in range(3)]
    assert infer_rules(mixed).industry_accounts == {}   # 편중 부족


# ── 산출물 ──

def test_render_and_proposal_have_no_pii():
    rows = [_row(거래처="유니크상호명칭", 사업자등록번호="111-11-11111",
                 업태="주유소", 차변계정="(판)차량유지비") for _ in range(5)]
    rep = infer_rules(rows)
    text = render_inference(rep)
    blob = text + str(propose_config(rep))
    assert "유니크상호명칭" not in blob and "111-11-11111" not in blob
    assert "규칙 추정" in text and "추정입니다" in text
    prop = propose_config(rep)
    assert prop["업종별_계정_힌트"]["주유소"] == "(판)차량유지비"
    assert prop["확인필요_추정"]                      # 보류 항목별 추정 포함
