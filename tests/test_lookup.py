"""대행사 건의 원본 결제내역 되찾기 — 합성 데이터만 쓴다."""
import csv
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from kafa.lookup.merge import AMBIGUOUS, FOUND, MISSING, STILL_AGENT, merge_statements
from kafa.lookup.request import build_plan
from kafa.lookup.statements import (find_header, normalize_amount, normalize_date,
                                    read_statement)


# ── 표기 정규화: 카드사마다 날짜·금액 모양이 다르다 ──

@pytest.mark.parametrize("raw,want", [
    ("2026-07-15", "2026-07-15"), ("20260715", "2026-07-15"),
    ("2026.07.15", "2026-07-15"), ("2026/7/5", "2026-07-05"),
    ("2026년 7월 5일", "2026-07-05"), ("2026-07-15 13:22:01", "2026-07-15"),
    ("", ""), (None, ""), ("2026-13-40", ""), ("합계", ""),
])
def test_dates_land_on_one_shape(raw, want):
    assert normalize_date(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("1,234원", 1234), ("(1,234)", -1234), ("-1234", -1234),
    ("￦ 5,000", 5000), ("", 0), ("abc", 0), ("0", 0),
])
def test_amounts_land_on_one_shape(raw, want):
    assert normalize_amount(raw) == Decimal(want)


# ── 이용내역 읽기: 헤더가 첫 줄에 없다 ──

def _write_statement(path, header_rows, rows, encoding="utf-8-sig"):
    with Path(path).open("w", encoding=encoding, newline="") as fh:
        w = csv.writer(fh)
        for r in header_rows:
            w.writerow(r)
        for r in rows:
            w.writerow(r)


def test_header_is_found_below_a_title_block(tmp_path):
    """카드사 명세서는 제목·조회기간이 위에 붙는다."""
    f = tmp_path / "001_합성카드.csv"
    _write_statement(f, [["합성카드 이용내역"], ["조회기간: 2026.01~2026.07"], [],
                         ["이용일자", "이용가맹점", "이용금액", "승인번호"]],
                     [["2026.03.02", "행복상사", "11,000", "30001234"]])
    rows = read_statement(f)
    assert len(rows) == 1
    assert rows[0].날짜 == "2026-03-02" and rows[0].금액 == Decimal(11000)
    assert rows[0].가맹점 == "행복상사" and rows[0].승인번호 == "30001234"


def test_alternate_column_wording_still_reads(tmp_path):
    f = tmp_path / "002_다른카드.csv"
    _write_statement(f, [["승인일자", "이용하신곳", "승인금액"]],
                     [["20260415", "동네카페", "5500"]])
    rows = read_statement(f)
    assert rows[0].가맹점 == "동네카페" and rows[0].금액 == Decimal(5500)


def test_cp949_statement_reads(tmp_path):
    f = tmp_path / "003_구형카드.csv"
    _write_statement(f, [["이용일자", "가맹점명", "이용금액"]],
                     [["2026.05.06", "한빛문구", "3,300"]], encoding="cp949")
    assert read_statement(f)[0].가맹점 == "한빛문구"


def test_rows_without_a_date_or_amount_are_dropped(tmp_path):
    """소계·안내 줄은 데이터가 아니다."""
    f = tmp_path / "004.csv"
    _write_statement(f, [["이용일자", "가맹점명", "이용금액"]],
                     [["2026.05.06", "한빛문구", "3,300"],
                      ["", "소계", "3,300"], ["합계", "", ""]])
    assert len(read_statement(f)) == 1


def test_unknown_layout_says_what_to_fix(tmp_path):
    f = tmp_path / "005.csv"
    _write_statement(f, [["처음", "보는", "컬럼"]], [["a", "b", "c"]])
    with pytest.raises(ValueError, match="statements.yaml"):
        read_statement(f)


def test_find_header_reports_no_match():
    assert find_header([["가", "나"], ["다", "라"]]) == (-1, {})


# ── 의뢰서 만들기 ──

def _db(tmp_path, rows):
    path = tmp_path / "kafa.db"
    con = sqlite3.connect(path)
    con.execute("create table clients (client_id text primary key, name text, created_at text)")
    con.execute("create table vouchers (voucher_key text primary key, client_id text,"
                " period text, 거래일자 text, 거래처 text, 사업자번호 text, 품명 text,"
                " 업태 text, 종목 text, 공급가액 text, 세액 text, 비과세 text, 합계 text,"
                " 유형코드 int, 차변계정코드 int, 대변계정코드 int, 공제여부 text,"
                " 판정유형 text, 신뢰도 real, 추천근거 text, skipped int, skip_reason text,"
                " source_file text, ingested_at text)")
    for cid in {r[0] for r in rows}:
        con.execute("insert into clients values (?,?,?)", (cid, f"합성{cid}", ""))
    for i, (cid, 날짜, 거래처, 합계, skipped) in enumerate(rows):
        con.execute("insert into vouchers (voucher_key, client_id, 거래일자, 거래처,"
                    " 합계, 업태, 종목, skipped) values (?,?,?,?,?,'','',?)",
                    (f"k{i}", cid, 날짜, 거래처, 합계, skipped))
    con.commit()
    con.close()
    return path


def test_plan_covers_the_period_and_hides_names(tmp_path):
    db = _db(tmp_path, [
        ("c1", "2026-03-02", "토스페이먼츠 주식회사", "11000", 0),
        ("c1", "2026-05-09", "한국정보통신（주）", "22000", 0),
        ("c2", "2026-04-01", "주식회사 티머니", "1500", 0),
        ("c2", "2026-04-02", "행복상사", "9000", 0),          # 대행사 아님 → 제외
    ])
    plans = build_plan(db, tmp_path / "out")
    assert [p.건수 for p in plans] == [2, 1]
    assert plans[0].기간_시작 == "2026-03-02" and plans[0].기간_끝 == "2026-05-09"

    seen = (tmp_path / "out" / "lookup_plan.csv").read_text(encoding="utf-8-sig")
    assert "합성c1" not in seen and "토스페이먼츠" not in seen   # 조수는 실명을 못 본다
    assert "합성c1" in (tmp_path / "out" / "lookup_index.csv").read_text(encoding="utf-8-sig")


def test_plan_skips_rows_that_are_not_vouchers(tmp_path):
    db = _db(tmp_path, [("c1", "2026-03-02", "토스페이먼츠 주식회사", "11000", 1)])
    assert build_plan(db, tmp_path / "out") == []


def test_plan_can_narrow_to_one_group(tmp_path):
    db = _db(tmp_path, [
        ("c1", "2026-03-02", "토스페이먼츠 주식회사", "11000", 0),
        ("c1", "2026-03-03", "주식회사 티머니", "1500", 0),
    ])
    plans = build_plan(db, tmp_path / "out", groups={"결제대행"})
    assert len(plans) == 1 and plans[0].건수 == 1


# ── 대조 ──

def _targets(tmp_path, rows):
    path = tmp_path / "lookup_targets.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "client_id", "수임처", "거래일자", "합계", "거래처", "갈래"])
        w.writerows(rows)
    return path


def test_merge_finds_the_real_merchant(tmp_path):
    t = _targets(tmp_path, [["1", "c1", "합성c1", "2026-03-02", "11000",
                             "토스페이먼츠 주식회사", "결제대행"]])
    s = tmp_path / "001_합성카드.csv"
    _write_statement(s, [["이용일자", "가맹점명", "이용금액", "승인번호"]],
                     [["2026.03.02", "행복상사", "11,000", "30001234"]])
    rows, report = merge_statements(t, [s], tmp_path / "r.csv")
    assert rows[0].결과 == FOUND and rows[0].가맹점 == "행복상사"
    assert rows[0].승인번호 == "30001234" and rows[0].해소됨
    assert report.찾음 == 1 and report.해소율 == 100.0


def test_merge_flags_a_statement_that_also_only_shows_an_agent(tmp_path):
    """카드사 내역에도 대행사뿐이면 그 소스는 소용없다 — 그렇게 적는다."""
    t = _targets(tmp_path, [["1", "c1", "합성c1", "2026-03-02", "11000",
                             "토스페이먼츠 주식회사", "결제대행"]])
    s = tmp_path / "001_합성카드.csv"
    _write_statement(s, [["이용일자", "가맹점명", "이용금액"]],
                     [["2026.03.02", "토스페이먼츠(주)", "11,000"]])
    rows, report = merge_statements(t, [s], tmp_path / "r.csv")
    assert rows[0].결과 == STILL_AGENT and not rows[0].해소됨
    assert report.여전히대행사 == 1 and report.찾음 == 0


def test_merge_reports_a_row_it_cannot_find(tmp_path):
    t = _targets(tmp_path, [["1", "c1", "합성c1", "2026-03-02", "11000", "토스페이먼츠", "결제대행"]])
    s = tmp_path / "001_합성카드.csv"
    _write_statement(s, [["이용일자", "가맹점명", "이용금액"]],
                     [["2026.03.09", "행복상사", "11,000"]])
    rows, report = merge_statements(t, [s], tmp_path / "r.csv")
    assert rows[0].결과 == MISSING and report.못찾음 == 1


def test_merge_marks_same_day_same_amount_as_ambiguous(tmp_path):
    """같은 날 같은 금액이 둘이면 찍지 않고 사람에게 넘긴다."""
    t = _targets(tmp_path, [["1", "c1", "합성c1", "2026-03-02", "11000", "토스페이먼츠", "결제대행"]])
    s = tmp_path / "001_합성카드.csv"
    _write_statement(s, [["이용일자", "가맹점명", "이용금액"]],
                     [["2026.03.02", "행복상사", "11,000"],
                      ["2026.03.02", "푸른식당", "11,000"]])
    rows, report = merge_statements(t, [s], tmp_path / "r.csv")
    assert rows[0].결과 == AMBIGUOUS and report.중복 == 1
    assert "행복상사" in rows[0].가맹점 and "푸른식당" in rows[0].가맹점


def test_file_number_keeps_clients_apart(tmp_path):
    """같은 날 같은 금액이라도 다른 수임처 명세서와 섞이지 않는다."""
    t = _targets(tmp_path, [
        ["1", "c1", "합성c1", "2026-03-02", "11000", "토스페이먼츠", "결제대행"],
        ["2", "c2", "합성c2", "2026-03-02", "11000", "토스페이먼츠", "결제대행"]])
    a = tmp_path / "001_카드.csv"
    b = tmp_path / "002_카드.csv"
    _write_statement(a, [["이용일자", "가맹점명", "이용금액"]], [["2026.03.02", "행복상사", "11000"]])
    _write_statement(b, [["이용일자", "가맹점명", "이용금액"]], [["2026.03.02", "푸른식당", "11000"]])
    rows, report = merge_statements(t, [a, b], tmp_path / "r.csv")
    assert [r.가맹점 for r in rows] == ["행복상사", "푸른식당"]
    assert report.찾음 == 2


def test_refund_rows_match_by_absolute_amount(tmp_path):
    """환불은 명세서에서 음수로 온다 — 부호가 달라도 붙인다."""
    t = _targets(tmp_path, [["1", "c1", "합성c1", "2026-03-02", "11000", "토스페이먼츠", "결제대행"]])
    s = tmp_path / "001_카드.csv"
    _write_statement(s, [["이용일자", "가맹점명", "이용금액"]],
                     [["2026.03.02", "행복상사", "-11,000"]])
    rows, _ = merge_statements(t, [s], tmp_path / "r.csv")
    assert rows[0].결과 == FOUND


def test_merge_writes_a_csv_the_person_can_read(tmp_path):
    t = _targets(tmp_path, [["1", "c1", "합성c1", "2026-03-02", "11000", "토스페이먼츠", "결제대행"]])
    s = tmp_path / "001_카드.csv"
    _write_statement(s, [["이용일자", "가맹점명", "이용금액"]], [["2026.03.02", "행복상사", "11000"]])
    out = tmp_path / "r.csv"
    merge_statements(t, [s], out)
    written = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert written[0]["찾은가맹점"] == "행복상사" and written[0]["수임처"] == "합성c1"
    assert written[0]["결과"] == FOUND
