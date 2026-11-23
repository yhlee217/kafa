"""유형(카과/일반) 추천 — 아직 처리 전인 전표의 유형을 같은 가맹점 이력으로 채운다. 합성 데이터만."""
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import openpyxl

from kafa.cli import classify_rows
from kafa.recommend.type_seed import TypeIndex, build_type_index_from_inputrows
from kafa.rules.models import Deduct, InputRow


def _row(거래처="행복상사", 유형="일반", 국세청="불공제", 차변계정="", 전표상태="",
         bizno="111-11-11119", 일자="03-15"):
    return InputRow(연도="2026", 일자=일자, 거래처=거래처, 공급가액=Decimal(10000),
                    세액=Decimal(1000), 합계=Decimal(11000), 국세청=국세청, 유형=유형,
                    차변계정=차변계정, 전표상태=전표상태, 사업자등록번호=bizno,
                    업태="도매", 종목="문구")


def _done(유형="카과", 거래처="행복상사", bizno="111-11-11119"):
    """담당자가 처리한 행 — 차변계정이 채워져 있다."""
    return _row(거래처=거래처, 유형=유형, 국세청="불공제", 차변계정="(판)소모품비", bizno=bizno)


# ── 이력에서 고르기 ──

def test_needs_enough_history_and_a_clear_majority():
    idx = TypeIndex()
    idx.add("행복상사", "111-11-11119", "카과")
    assert idx.suggest("행복상사", "111-11-11119") is None            # 1건 — 근거 부족
    idx.add("행복상사", "111-11-11119", "카과")
    hit = idx.suggest("행복상사", "111-11-11119")
    assert hit.label == "카과" and hit.support == 2 and hit.basis == "사업자번호"


def test_split_history_is_left_alone():
    """같은 가맹점인데 담당자가 때마다 다르게 처리했다면 추천하지 않는다."""
    idx = TypeIndex()
    for label in ("카과", "카과", "일반"):
        idx.add("행복상사", "111-11-11119", label)
    assert idx.suggest("행복상사", "111-11-11119") is None


def test_falls_back_to_the_vendor_name_when_there_is_no_bizno():
    idx = TypeIndex()
    for _ in range(2):
        idx.add("행복 상사", "", "카과")
    assert idx.suggest("행복상사", "").basis == "거래처명"


# ── 분류에 적용 ──

def test_unprocessed_row_takes_the_type_its_merchant_was_given_before():
    """처리 전 행은 유형이 국세청 값(일반) 그대로다. 같은 가맹점을 카과로 처리해 왔으면 카과."""
    rows = [_done(), _done(), _row()]
    out, _ = classify_rows(rows, client_type="corporate")
    c = out[-1]
    assert c.source.유형 == "카과" and c.유형코드 == 57
    assert c.공제여부 == Deduct.DEDUCTIBLE                 # 유형이 공제 여부를 가른다
    assert "TYPE-HIST" in c.판정근거
    assert "같은 가맹점 이력" in c.추천근거


def test_processed_rows_keep_the_type_they_were_given():
    """처리된 행의 유형은 담당자 결정이다 — 이력이 달라도 건드리지 않는다."""
    rows = [_done(), _done(), _done(유형="일반")]
    out, _ = classify_rows(rows, client_type="corporate")
    assert out[-1].source.유형 == "일반" and "TYPE-HIST" not in out[-1].판정근거


def test_duplicates_and_unknown_merchants_are_left_alone():
    rows = [_done(), _done(), _row(전표상태="중복전표"), _row(거래처="처음보는가게", bizno="")]
    out, _ = classify_rows(rows, client_type="corporate")
    assert all("TYPE-HIST" not in c.판정근거 for c in out[2:])


def test_only_processed_rows_feed_the_history():
    """도구가 추천한 행(처리 전)을 다시 배우면 추천이 추천을 키운다."""
    idx = build_type_index_from_inputrows([_row(유형="카과"), _row(유형="카과"), _done()])
    from kafa.recommend.seed import bizno_key
    assert sum(idx.by_bizno[bizno_key("111-11-11119")].values()) == 1


def test_switch_off_in_config(tmp_path):
    cfg = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", cfg)
    rules = cfg / "rules.yaml"
    rules.write_text(rules.read_text(encoding="utf-8").replace(
        "type_recommend:\n  enabled: true", "type_recommend:\n  enabled: false"), encoding="utf-8")
    out, _ = classify_rows([_done(), _done(), _row()], client_type="corporate",
                           config_dir=str(cfg))
    assert out[-1].source.유형 == "일반"


# ── 지난달 이력이 이번 달에 쓰인다 (DB) ──

def _xlsx(path, rows):
    from kafa.io_wehago.schema import INPUT_COLUMNS
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(INPUT_COLUMNS)
    for r in rows:
        m = {"연도": r.연도, "일자": r.일자, "Code": "A", "거래처": r.거래처, "구분": "법인",
             "품명": "", "공급가액": 10000, "세액": 1000, "비과세": 0, "합계": 11000,
             "국세청": r.국세청, "업태": r.업태, "종목": r.종목, "유형": r.유형,
             "차변계정": r.차변계정, "전표상태": r.전표상태, "사업자등록번호": r.사업자등록번호}
        ws.append([m.get(c, "") for c in INPUT_COLUMNS])
    wb.save(path)


def test_last_months_processing_guides_this_months_unprocessed_row(tmp_path):
    from kafa.pipeline.runner import run_pipeline

    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "2월.xlsx",
          [_row(유형="카과", 차변계정="(판)소모품비", 일자=f"02-0{d}") for d in (2, 3)])
    assert run_pipeline(inbox, out).ok
    _xlsx(inbox / "고객001" / "3월.xlsx", [_row(일자="03-15")])         # 처리 전, 유형 일반
    assert run_pipeline(inbox, out).ok

    con = sqlite3.connect(out / "kafa.db")
    code, ded = con.execute(
        "select 유형코드, 공제여부 from vouchers where 거래일자='2026-03-15'").fetchone()
    assert (code, ded) == (57, "공제")


def test_store_returns_only_processed_history(tmp_path):
    from kafa.store.db import VoucherStore

    rows = classify_rows([_done(), _row(유형="카과")], client_type="corporate")[0]
    with VoucherStore(tmp_path / "k.db") as db:
        db.upsert_vouchers("c1", "2026-03", rows)
        assert db.type_records("c1") == [("행복상사", "111-11-11119", "카과")]
