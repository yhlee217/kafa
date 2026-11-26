"""수임처별 식대 계정 — 담당자가 '식대는 접대비'로 정한 수임처. 합성 데이터만."""
from decimal import Decimal

import yaml

from kafa.cli import classify_rows
from kafa.clients import resolve_account_text
from kafa.clients_cli import main as clients_main
from kafa.config_loader import local_clients_path
from kafa.recommend.recommender import Recommendation, Recommender
from kafa.rules.models import Deduct, InputRow, Verdict


class _Fixed(Recommender):
    def __init__(self, code):
        self.code = code

    def recommend_for(self, row):
        return Recommendation(self.code, 0.6, "같은 업종 이력", resolved=True)


def _row(업태="음식점업", 종목="한식", 차변계정="", 유형="카과"):
    return InputRow(연도="2026", 일자="03-15", 거래처="합성식당", 공급가액=Decimal(10000),
                    세액=Decimal(1000), 합계=Decimal(11000), 국세청="공제", 유형=유형,
                    차변계정=차변계정, 전표상태="미추천" if not 차변계정 else "",
                    사업자등록번호="111-11-11119", 업태=업태, 종목=종목)


MEAL = {"client_type": "corporate", "has_employees": True, "meal_account": 813}


def _one(row, code=811, profile=MEAL):
    (c,), _ = classify_rows([row], recommender=_Fixed(code), profile=profile)
    return c


def test_recommended_welfare_meal_becomes_entertainment_and_non_deductible():
    c = _one(_row())
    assert c.차변계정코드 == 813 and c.판정유형 == Verdict.RECOMMENDED
    assert "CLIENT-MEAL" in c.판정근거 and "수임처 식대 계정" in c.추천근거
    # 접대비면 불공제·유형 일반이 따라온다(DED-007)
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE and c.유형코드 == 3


def test_rows_already_filled_by_wehago_are_left_alone():
    c = _one(_row(차변계정="(판)복리후생비"))
    assert c.차변계정코드 == 811 and "CLIENT-MEAL" not in c.판정근거


def test_only_meals_change():
    assert _one(_row(업태="도매", 종목="문구")).차변계정코드 == 811          # 식대 아님
    assert _one(_row(업태="숙박 및 음식점업", 종목="호텔")).차변계정코드 == 811  # 숙박
    assert _one(_row(종목="")).차변계정코드 == 813                            # 종목 없으면 업태로
    assert _one(_row(), code=830).차변계정코드 == 830                          # 복리후생비 아님


def test_clients_without_a_meal_account_are_unchanged():
    assert _one(_row(), profile={"client_type": "corporate"}).차변계정코드 == 811


def test_resolve_account_text():
    assert resolve_account_text("접대비") == (813, "(판)접대비(기업업무추진비)")
    assert resolve_account_text("813")[0] == 813
    assert resolve_account_text("촬영비")[0] is None                  # 코드표에 없음
    code, why = resolve_account_text("262")
    assert code is None and "대변" in why


def _store(clients):
    p = local_clients_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"defaults": {"client_type": "corporate"},
                                 "clients": clients}, allow_unicode=True), encoding="utf-8")
    return p


def test_meal_command_by_note_sets_and_clears(capsys):
    p = _store({"가나식당": {"client_type": "corporate", "note": "식대-접대비처리"},
                "다라상사": {"client_type": "individual", "note": "폐업"}})
    assert clients_main(["meal", "접대비", "--where-note", "식대-접대비"]) == 0
    c = yaml.safe_load(p.read_text(encoding="utf-8"))["clients"]
    assert c["가나식당"]["meal_account"] == 813 and "meal_account" not in c["다라상사"]
    said = capsys.readouterr().out
    assert "식대 계정 → (판)접대비(기업업무추진비) [813]" in said and "가나식당" not in said

    assert clients_main(["meal", "--where-note", "식대-접대비", "--clear"]) == 0
    c = yaml.safe_load(p.read_text(encoding="utf-8"))["clients"]
    assert "meal_account" not in c["가나식당"]


def test_meal_command_refuses_ambiguous_or_unknown(capsys):
    _store({"가나식당": {"note": "식대 메모"}, "다라식당": {"note": "식대 메모"}})
    assert clients_main(["meal", "접대비", "--where-note", "식대"]) == 2
    assert clients_main(["meal", "촬영비", "--client", "가나식당"]) == 2
    err = capsys.readouterr().err
    assert "2곳" in err and "촬영비" in err


def test_meal_command_by_client_name_accepts_corp_mark_variant():
    p = _store({"(주)가나식당": {"client_type": "corporate"}})
    assert clients_main(["meal", "813", "--client", "가나식당"]) == 0
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["clients"]["(주)가나식당"]["meal_account"] == 813
