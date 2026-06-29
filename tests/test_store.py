"""베이스 데이터 저장소(SQLite) — 누적·멱등·고객 분리."""
from decimal import Decimal

from kafa.rules.models import ClassifiedRow, Deduct, InputRow, Verdict
from kafa.store.db import VoucherStore


def _c(거래처="가맹A", 합계="11000", code=811, skipped=False):
    s = InputRow(연도="2026", 일자="03-15", 거래처=거래처, 품명="커피",
                 공급가액=Decimal("10000"), 세액=Decimal("1000"), 합계=Decimal(합계),
                 사업자등록번호="111-11-11119")
    return ClassifiedRow(차변계정코드=code, 공제여부=Deduct.DEDUCTIBLE,
                         판정유형=Verdict.RULE_CONFIRMED, source=s,
                         skipped=skipped, skip_reason="중복전표" if skipped else "")


def test_store_accumulate_and_idempotent(tmp_path):
    with VoucherStore(tmp_path / "kafa.db") as db:
        rows = [_c("가맹A"), _c("가맹B")]
        r1 = db.upsert_vouchers("고객1", "2026-03", rows, source_file="a.xlsx")
        assert (r1.inserted, r1.existing) == (2, 0) and db.count() == 2

        # 같은 데이터 재적재 → 기존 무시(멱등), 총량 불변
        r2 = db.upsert_vouchers("고객1", "2026-03", rows, source_file="a.xlsx")
        assert (r2.inserted, r2.existing) == (0, 2) and db.count() == 2


def test_store_client_isolation(tmp_path):
    with VoucherStore(tmp_path / "kafa.db") as db:
        db.upsert_vouchers("고객1", "2026-03", [_c("가맹A"), _c("가맹B")])
        # 같은 거래처라도 고객이 다르면 별도 키 → 별도 누적
        db.upsert_vouchers("고객2", "2026-03", [_c("가맹A")])
        assert db.count() == 3
        assert db.count("고객1") == 2 and db.count("고객2") == 1
        assert set(db.clients()) == {"고객1", "고객2"}


def test_store_period_accumulates(tmp_path):
    with VoucherStore(tmp_path / "kafa.db") as db:
        db.upsert_vouchers("고객1", "2026-03", [_c("가맹A")])
        # 다른 달 같은 거래처(다른 거래일자라면) → 다른 키. 여기선 합계만 바꿔 키 분리.
        db.upsert_vouchers("고객1", "2026-04", [_c("가맹A", 합계="22000")])
        assert db.count("고객1") == 2
