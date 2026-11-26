"""유형 기준표(type_policy) — 담당자 기준(계정 × 수임처 속성 × 세액). 합성 데이터만."""
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from kafa.cli import classify_rows
from kafa.config_loader import load_rules
from kafa.learn.type_check import check_type_policy, render
from kafa.rules.models import Deduct, InputRow
from kafa.rules.type_policy import decide_type, policy

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _row(차변계정="(판)소모품비", 유형="일반", 국세청="불공제", 세액=1000, 전표상태="확정가능",
         비과세=0, 업태="도매", 종목="문구"):
    return InputRow(연도="2026", 일자="03-15", 거래처="합성가맹점", 공급가액=Decimal(10000),
                    세액=Decimal(세액), 비과세=Decimal(비과세), 합계=Decimal(10000 + 세액),
                    국세청=국세청, 유형=유형, 차변계정=차변계정, 전표상태=전표상태,
                    사업자등록번호="111-11-11119", 업태=업태, 종목=종목)


@pytest.fixture
def on(tmp_path):
    """기준표를 켠 config 복사본."""
    d = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, d)
    p = d / "rules.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["type_policy"]["enabled"] = True
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    load_rules.cache_clear()
    yield str(d)
    load_rules.cache_clear()


# ── 기준표 ──

@pytest.mark.parametrize("code, vat, prof, want", [
    (813, True, {}, "일반"),                                  # 접대비 — 법
    (830, True, {}, "카과"),                                  # 소모품비
    (146, True, {}, "카과"),                                  # 재료비
    (814, True, {}, "카과"),                                  # 통신비
    (811, True, {"has_employees": True}, "카과"),             # 복리후생비 — 직원 있음
    (811, True, {"has_employees": False}, "일반"),
    (822, True, {"cargo_vehicle": True}, "카과"),             # 차량유지비 — 화물차
    (822, True, {}, "일반"),
    (830, False, {}, "일반"),                                 # 세액 0 → 공제할 것 없음
    (146, False, {"restaurant": True}, "카면"),               # 음식점 면세 재료
    (146, False, {}, "일반"),
    (831, True, {}, None),                                    # 지급수수료 — 기준표에 없음
])
def test_decide_type(code, vat, prof, want):
    d = decide_type(code, vat, prof, policy())
    assert (d.label if d else None) == want


def test_policy_is_off_until_the_father_confirms():
    assert policy()["enabled"] is False
    c = classify_rows([_row()], client_type="corporate")[0][0]
    assert c.유형코드 == 3 and "TYPE-POLICY" not in c.판정근거


# ── 켰을 때 ──

def test_wehago_default_type_is_replaced(on):
    c = classify_rows([_row()], client_type="corporate", config_dir=on)[0][0]
    assert c.유형코드 == 57 and c.공제여부 == Deduct.DEDUCTIBLE
    assert "TYPE-POLICY" in c.판정근거 and "담당자 기준" in c.추천근거


def test_fathers_types_and_guards_are_left_alone(on):
    rows = [_row(전표상태="전표확정"), _row(전표상태="담당자확정"),
            _row(비과세=500)]                                  # 봉사료(DED-005) — 접대성
    got = classify_rows(rows, client_type="corporate", config_dir=on)[0]
    assert [c.유형코드 for c in got] == [3, 3, 3]
    assert all("TYPE-POLICY" not in c.판정근거 for c in got)


def test_entertainment_stays_non_deductible_and_welfare_follows_employees(on):
    rows = [_row(차변계정="(판)접대비(기업업무추진비)", 유형="카과", 국세청="공제"),
            _row(차변계정="(판)복리후생비")]
    ent, welfare = classify_rows(rows, client_type="corporate", config_dir=on,
                                 profile={"has_employees": True})[0]
    assert ent.유형코드 == 3 and ent.공제여부 == Deduct.NON_DEDUCTIBLE
    assert welfare.유형코드 == 57
    alone = classify_rows([_row(차변계정="(판)복리후생비", 유형="카과", 국세청="공제")],
                          client_type="corporate", config_dir=on,
                          profile={"has_employees": False})[0][0]
    assert alone.유형코드 == 3


# ── 맞춰 보기 ──

def test_check_against_fathers_rows():
    records = [("c1", 146, "1000", 57), ("c1", 146, "1000", 57), ("c1", 146, "0", 58),
               ("c1", 813, "1000", 3), ("c2", 831, "1000", 57), ("c2", 822, "1000", 57)]
    r = check_type_policy(records)
    assert (r.rows, r.clients, r.decided, r.agree) == (6, 2, 5, 3)
    text = render(r)
    assert "60%" in text and "차량유지비 · 일반 → 카과: 1건" in text
    assert "재료비 · 일반 → 카면: 1건" in text and "c1" not in text
