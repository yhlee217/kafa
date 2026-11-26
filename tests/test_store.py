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


# ── 같은 날 같은 금액을 두 번 결제하는 일은 실제로 일어난다 ──

def _row(거래처="행복상사", 합계="6500", 일자="03-02", 품명="", 전표상태=""):
    from decimal import Decimal

    from kafa.rules.engine import classify_row
    from kafa.rules.models import InputRow
    return classify_row(InputRow(
        연도="2026", 일자=일자, 거래처=거래처, 공급가액=Decimal(합계),
        세액=Decimal(0), 합계=Decimal(합계), 국세청="공제", 유형="카과",
        차변계정="(판)복리후생비", 전표상태=전표상태, 사업자등록번호="000-00-00000",
        품명=품명), client_type="corporate")


def test_two_identical_purchases_are_both_kept(tmp_path):
    """편의점에서 같은 걸 두 번 사면 전표도 두 줄이다 — 하나가 사라지면 안 된다."""
    from kafa.store.db import VoucherStore

    with VoucherStore(tmp_path / "k.db") as db:
        res = db.upsert_vouchers("c1", "2026-03", [_row(), _row()])
        assert res.inserted == 2
        assert db.count("c1") == 2


def test_reingesting_the_same_file_stays_idempotent(tmp_path):
    """순번을 붙여도 같은 파일을 다시 넣으면 늘지 않는다."""
    from kafa.store.db import VoucherStore

    rows = [_row(), _row(), _row(거래처="다른가게")]
    with VoucherStore(tmp_path / "k.db") as db:
        assert db.upsert_vouchers("c1", "2026-03", rows).inserted == 3
        again = db.upsert_vouchers("c1", "2026-03", rows)
        assert again.inserted == 0 and again.existing == 3
        assert db.count("c1") == 3


def test_a_skipped_row_never_shadows_the_real_one(tmp_path):
    """위하고가 '중복전표'로 표시한 행이 먼저 들어와도 정상 행을 가리지 않는다."""
    from kafa.store.db import VoucherStore

    dup = _row(전표상태="중복전표")
    assert dup.skipped
    with VoucherStore(tmp_path / "k.db") as db:
        db.upsert_vouchers("c1", "2026-03", [dup])
        res = db.upsert_vouchers("c1", "2026-03", [_row()])
        assert res.upgraded == 1 and db.count("c1") == 1
    import sqlite3
    con = sqlite3.connect(tmp_path / "k.db")
    assert con.execute("select skipped from vouchers").fetchone()[0] == 0


def test_a_good_row_is_not_downgraded_by_a_later_duplicate(tmp_path):
    """반대 방향으로는 덮어쓰지 않는다 — 좋은 분류를 지킨다(first-wins)."""
    from kafa.store.db import VoucherStore

    with VoucherStore(tmp_path / "k.db") as db:
        db.upsert_vouchers("c1", "2026-03", [_row()])
        res = db.upsert_vouchers("c1", "2026-03", [_row(전표상태="중복전표")])
        assert res.upgraded == 0 and res.existing == 1
    import sqlite3
    con = sqlite3.connect(tmp_path / "k.db")
    assert con.execute("select skipped from vouchers").fetchone()[0] == 0


def test_the_first_row_keeps_the_old_key_shape(tmp_path):
    """순번을 붙여도 첫 행의 키는 그대로다 — 기존 DB 와 호환된다."""
    from kafa.store.db import _voucher_key

    c = _row()
    assert _voucher_key("c1", c) == _voucher_key("c1", c, 0)
    assert _voucher_key("c1", c, 1) != _voucher_key("c1", c, 0)


# ── 확정출처(누가 정했나) ──

def _src_row(vendor="행복상사", bizno="111-11-11119", 일자="03-15", 차변계정="(판)소모품비",
             전표상태="확정가능", 유형="일반"):
    from decimal import Decimal

    from kafa.rules.models import InputRow
    return InputRow(연도="2026", 일자=일자, 거래처=vendor, 공급가액=Decimal(10000),
                    세액=Decimal(1000), 합계=Decimal(11000), 국세청="불공제", 유형=유형,
                    차변계정=차변계정, 전표상태=전표상태, 사업자등록번호=bizno, 업태="도매", 종목="문구")


def test_confirm_source_is_recorded(tmp_path):
    from kafa.cli import classify_rows
    from kafa.store.db import VoucherStore
    rows = classify_rows([_src_row(), _src_row(일자="03-16", 전표상태="전표확정"),
                          _src_row(일자="03-17", 전표상태="담당자확정"),
                          _src_row(일자="03-18", 차변계정="", 전표상태="미추천")],
                         client_type="corporate")[0]
    with VoucherStore(tmp_path / "k.db") as db:
        db.upsert_vouchers("c1", "2026-03", rows)
        assert db.source_counts() == {"위하고": 1, "전표확정": 1, "담당자": 1}
        got = dict(db._conn.execute("SELECT 거래일자, 전표상태 FROM vouchers").fetchall())
    assert got["2026-03-18"] == "미추천"


def test_seed_prefers_fathers_answer_for_the_same_vendor(tmp_path):
    """같은 가맹점에 위하고 자동 분류와 담당자 답이 다르면 담당자 답만 이력으로 쓴다."""
    from kafa.cli import classify_rows
    from kafa.store.db import VoucherStore
    rows = classify_rows([_src_row(차변계정="(판)소모품비"),
                          _src_row(일자="03-16", 차변계정="(판)소모품비"),
                          _src_row(일자="03-17", 차변계정="(판)도서인쇄비", 전표상태="담당자확정"),
                          _src_row(vendor="다른상사", bizno="999-99-99999", 일자="03-18")],
                         client_type="corporate")[0]
    with VoucherStore(tmp_path / "k.db") as db:
        db.upsert_vouchers("c1", "2026-03", rows)
        seed = db.seed_records("c1")
    by_vendor = {}
    for vendor, _bizno, code, *_ in seed:
        by_vendor.setdefault(vendor, []).append(code)
    assert by_vendor == {"행복상사": [826], "다른상사": [830]}


def test_old_db_is_migrated_and_backfilled_from_originals(tmp_path):
    """확정출처가 없던 DB — 처리 행은 위하고로 채우고, 원본의 전표확정은 되살린다."""
    import sqlite3

    from kafa.cli import classify_rows
    from kafa.store.db import VoucherStore
    path = tmp_path / "old.db"
    src = [_src_row(), _src_row(일자="03-16", 전표상태="전표확정", 유형="카과")]
    with VoucherStore(path) as db:
        db.upsert_vouchers("c1", "2026-03", classify_rows(src, client_type="corporate")[0])
    con = sqlite3.connect(path)                     # 예전 DB 흉내: 두 칸을 비운다
    con.execute("UPDATE vouchers SET 전표상태=NULL, 확정출처=NULL")
    con.commit()
    con.execute("ALTER TABLE vouchers DROP COLUMN 확정출처")
    con.commit()
    con.close()
    with VoucherStore(path) as db:
        assert db.source_counts() == {"위하고": 2}
        assert db.type_records("c1") == []
        assert db.backfill_status("c1", src) == 2
        assert db.backfill_status("c1", src) == 0          # 다시 해도 그대로
        assert db.source_counts() == {"위하고": 1, "전표확정": 1}
        assert db.type_records("c1") == [("행복상사", "111-11-11119", "카과")]


def test_backfill_command_reads_the_archive(tmp_path, capsys):
    import openpyxl

    from kafa.cli import classify_rows
    from kafa.io_wehago.schema import INPUT_COLUMNS
    from kafa.store.backfill import main
    from kafa.store.db import VoucherStore
    src = [_src_row(), _src_row(일자="03-16", 전표상태="전표확정", 유형="카과")]
    db = tmp_path / "kafa.db"
    with VoucherStore(db) as s:
        s.upsert_vouchers("고객A", "2026-03", classify_rows(src, client_type="corporate")[0])
        s._conn.execute("UPDATE vouchers SET 전표상태=NULL, 확정출처='위하고'")
        s._conn.commit()
    f = tmp_path / "_archive" / "고객A" / "3월.xlsx"
    f.parent.mkdir(parents=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(INPUT_COLUMNS)
    for r in src:
        m = {"연도": r.연도, "일자": r.일자, "거래처": r.거래처, "공급가액": 10000, "세액": 1000,
             "비과세": 0, "합계": 11000, "국세청": r.국세청, "업태": r.업태, "종목": r.종목,
             "유형": r.유형, "차변계정": r.차변계정, "전표상태": r.전표상태,
             "사업자등록번호": r.사업자등록번호}
        ws.append([m.get(c, "") for c in INPUT_COLUMNS])
    wb.save(f)
    assert main(["--archive", str(tmp_path / "_archive"), "--db", str(db)]) == 0
    said = capsys.readouterr().out
    assert "전표상태를 채운 행 2건" in said and "전표확정 1" in said and "고객A" not in said
