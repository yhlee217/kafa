"""고객 진행 현황 보드 — 집계·렌더·파일 생성."""
from decimal import Decimal

from kafa.pipeline.summary import build_board, render_board_text, write_board
from kafa.rules.models import ClassifiedRow, Deduct, InputRow, Verdict
from kafa.store.db import VoucherStore


def _c(거래처, 합계="11000", 공제=Deduct.DEDUCTIBLE, 판정=Verdict.RULE_CONFIRMED):
    s = InputRow(연도="2026", 일자="03-15", 거래처=거래처, 품명="x",
                 공급가액=Decimal("10000"), 세액=Decimal("1000"), 합계=Decimal(합계),
                 사업자등록번호="111-11-11119")
    return ClassifiedRow(차변계정코드=811, 공제여부=공제, 판정유형=판정, source=s)


def test_board_aggregates(tmp_path):
    with VoucherStore(tmp_path / "kafa.db") as db:
        db.upsert_vouchers("고객1", "2026-03", [
            _c("A"),
            _c("B", 공제=Deduct.REVIEW),
            _c("C", 판정=Verdict.UNRESOLVED, 합계="12000"),
        ])
        db.upsert_vouchers("고객2", "2026-04", [_c("A")])
        rows = build_board(db)
    by = {r["client_id"]: r for r in rows}
    assert by["고객1"]["vouchers"] == 3
    assert by["고객1"]["확인필요"] == 2          # 검토 1 + 미해소 1
    assert by["고객2"]["latest_period"] == "2026-04"
    assert "고객 진행 현황" in render_board_text(rows)


def test_write_board_files(tmp_path):
    with VoucherStore(tmp_path / "kafa.db") as db:
        db.upsert_vouchers("행복상사", "2026-03", [_c("A")])
    p = write_board(tmp_path)
    assert p.exists() and (tmp_path / "_board.txt").exists()
    html = p.read_text(encoding="utf-8")
    assert "행복상사" in html and "고객 진행 현황" in html


def test_write_board_no_db(tmp_path):
    assert write_board(tmp_path) is None     # kafa.db 없으면 None
