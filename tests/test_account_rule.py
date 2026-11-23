"""계정이 공제 여부를 정하는 경우 — 접대비는 매입세액 불공제(부가세법). 합성 데이터만."""
import shutil
from decimal import Decimal
from pathlib import Path

from kafa.cli import classify_rows
from kafa.recommend.recommender import build_recommender
from kafa.recommend.seed import build_seed_from_inputrows
from kafa.rules.engine import classify_row
from kafa.rules.models import Deduct, InputRow


def _row(차변계정="(판)접대비(기업업무추진비)", 유형="카과", 국세청="공제", 합계=11000,
         거래처="합성카페", 업태="음식점업", 종목="커피", bizno="111-11-11119"):
    return InputRow(연도="2026", 일자="03-15", 거래처=거래처, 공급가액=Decimal(합계),
                    세액=Decimal(0), 합계=Decimal(합계), 국세청=국세청, 유형=유형,
                    차변계정=차변계정, 전표상태="", 사업자등록번호=bizno,
                    업태=업태, 종목=종목)


def test_entertainment_filled_by_wehago_is_non_deductible():
    """유형이 카과·국세청이 공제여도, 계정이 접대비면 불공제·유형 일반."""
    c = classify_row(_row(), client_type="corporate")
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE and c.유형코드 == 3
    assert "DED-007" in c.판정근거


def test_every_entertainment_account_is_covered():
    for name in ("(제)접대비(기업업무추진비)", "(도)접대비(기업업무추진비)",
                 "(판)해외접대비(기업업무추진비)"):
        c = classify_row(_row(차변계정=name), client_type="corporate")
        assert c.공제여부 == Deduct.NON_DEDUCTIBLE, name


def test_other_accounts_are_left_to_the_type():
    c = classify_row(_row(차변계정="(판)소모품비"), client_type="corporate")
    assert c.공제여부 == Deduct.DEDUCTIBLE and c.유형코드 == 57
    assert "DED-007" not in c.판정근거


def test_recommended_entertainment_is_non_deductible_too():
    """처리 전 행이 업종 이력으로 접대비를 추천받으면 — 실측 11건이 공제로 나가던 경우."""
    done = [_row(유형="일반", 국세청="불공제", 거래처=f"카페{i}", bizno=f"10{i}-11-11111")
            for i in range(4)]
    new = _row(차변계정="", 거래처="새카페", bizno="300-33-33333")     # 유형 카과·국세청 공제
    seed = build_seed_from_inputrows(done)
    out, _ = classify_rows(done + [new], client_type="corporate", seed=seed,
                           recommender=build_recommender(seed))
    c = out[-1]
    assert c.차변계정코드 == 813                                   # 업종 이력으로 접대비
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE and c.유형코드 == 3
    assert "DED-007" in c.판정근거


def test_refund_rows_look_at_the_expense_side():
    """환불은 차·대변이 뒤바뀐다 — 비용 계정(접대비)은 대변 쪽에 있어도 불공제다."""
    c = classify_row(_row(합계=-11000), client_type="corporate")
    assert c.reversal_applied and c.대변계정코드 == 813
    assert c.공제여부 == Deduct.NON_DEDUCTIBLE


def test_switch_off_in_config(tmp_path):
    cfg = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", cfg)
    rules = cfg / "rules.yaml"
    rules.write_text(rules.read_text(encoding="utf-8").replace(
        "  by_account:\n    접대비: 불공제", "  by_account: {}"), encoding="utf-8")
    c = classify_row(_row(), client_type="corporate", config_dir=str(cfg))
    assert c.공제여부 == Deduct.DEDUCTIBLE and c.유형코드 == 57
