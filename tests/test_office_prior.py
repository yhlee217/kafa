"""사무실 전체 이력 추천 — 다른 수임처들이 같은 업종을 처리한 계정. 합성 데이터만."""
import sqlite3

import openpyxl
import pytest

from kafa.recommend import office_prior
from kafa.recommend.office_prior import (
    BASIS_PREFIX,
    OfficePrior,
    OfficePriorRecommender,
    build_office_prior,
    check_prior,
)
from kafa.recommend.recommender import Recommendation, Recommender
from kafa.rules.models import InputRow

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _row(vendor, bizno, 업태="제조", 종목="인쇄", 차변="", 상태="", 일자="03-01"):
    return ["2026", 일자, "A", vendor, "법인", "", 10000, 1000, 0, 11000, "공제",
            업태, 종목, "카과", 차변, "", "", 상태, bizno]


def _prior(spec):
    """spec: {client: [(업태, 종목, code), ...]}"""
    p = OfficePrior()
    for client, items in spec.items():
        for u, j, code in items:
            p.add(client, u, j, code)
    return p


# ── 문턱 ──

def test_needs_several_clients_agreeing():
    p = _prior({"A": [("제조", "인쇄", 826)] * 4, "B": [("제조", "인쇄", 826)] * 2})
    assert p.suggest("제조", "인쇄", min_clients=3) is None            # 두 곳뿐
    p.add("C", "제조", "인쇄", 826)
    hit = p.suggest("제조", "인쇄", min_clients=3)
    assert (hit.code, hit.clients, hit.rows, hit.share, hit.agree) == (826, 3, 7, 1.0, 1.0)


def test_one_big_client_cannot_outvote_the_others():
    """건수로는 80%를 넘어도, 그 계정을 주로 쓴 수임처가 셋 중 하나면 쓰지 않는다."""
    p = _prior({"A": [("음식점", "한식", 811)] * 40,
                "B": [("음식점", "한식", 813)] * 3,
                "C": [("음식점", "한식", 813)] * 3})
    assert p.suggest("음식점", "한식", min_clients=3, min_share=0.8) is None


def test_split_industry_is_left_alone():
    p = _prior({c: [("음식점", "한식", 811 if c in "AB" else 813)] * 3 for c in "ABCD"})
    assert p.suggest("음식점", "한식", min_clients=3, min_share=0.8) is None


def test_needs_both_industry_fields():
    p = _prior({c: [("서비스", "", 831)] * 3 for c in "ABC"})
    assert p.empty                                     # 업태만으로는 키를 만들지 않는다


def test_exclude_client_for_leave_one_out():
    p = _prior({c: [("제조", "인쇄", 826)] for c in "ABC"})
    assert p.suggest("제조", "인쇄", min_clients=3) is not None
    assert p.suggest("제조", "인쇄", min_clients=3, exclude_client="A") is None


def test_agent_rows_are_not_learned():
    p = build_office_prior([(c, "토스페이먼츠", "서비스", "광고", 831) for c in "ABC"])
    assert p.empty


# ── 추천 순서 ──

class _Fixed(Recommender):
    def __init__(self, rec):
        self.rec = rec

    def recommend_for(self, row):
        return self.rec


def test_client_history_comes_first_and_basis_has_no_names():
    p = _prior({c: [("제조", "인쇄", 826)] for c in "ABC"})
    row = InputRow(거래처="가나다인쇄", 업태="제조", 종목="인쇄", 유형="카과")
    own = Recommendation(830, 0.9, "같은 가맹점 이력", resolved=True)
    assert OfficePriorRecommender(p, _Fixed(own)).recommend_for(row).account_code == 830

    none = Recommendation(None, 0.0, "근거 없음", resolved=False)
    rec = OfficePriorRecommender(p, _Fixed(none)).recommend_for(row)
    assert rec.resolved and rec.account_code == 826 and rec.confidence == 0.6   # 상한
    assert rec.basis.startswith(BASIS_PREFIX) and "가나다" not in rec.basis
    assert "3곳" in rec.basis


def test_agent_row_gets_nothing():
    p = _prior({c: [("서비스", "광고", 833)] for c in "ABC"})
    none = Recommendation(None, 0.0, "근거 없음", resolved=False)
    row = InputRow(거래처="토스페이먼츠", 업태="서비스", 종목="광고")
    assert not OfficePriorRecommender(p, _Fixed(none)).recommend_for(row).resolved


# ── 파이프라인 ──

def _history(inbox):
    """세 수임처가 인쇄업을 도서인쇄비로 처리한 이력."""
    for i, c in enumerate(("고객A", "고객B", "고객C")):
        _xlsx(inbox / c / "2월.xlsx",
              [_row(f"인쇄소{c}", f"11{i}-11-11111", 차변="(판)도서인쇄비", 일자="02-01")])


@pytest.fixture
def enabled(monkeypatch):
    def _on(config_dir=None):
        return {"enabled": True, "min_clients": 3, "min_share": 0.8, "confidence_cap": 0.6}
    monkeypatch.setattr(office_prior, "settings", _on)


def test_pipeline_fills_new_client_from_office_history(tmp_path, enabled):
    from kafa.pipeline.runner import run_pipeline
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _history(inbox)
    run_pipeline(inbox, out)
    _xlsx(inbox / "고객D" / "3월.xlsx", [_row("처음인쇄", "999-99-99999", 상태="미추천")])
    res = run_pipeline(inbox, out)
    assert res.ok
    con = sqlite3.connect(out / "kafa.db")
    code, verdict, basis = con.execute(
        "SELECT 차변계정코드, 판정유형, 추천근거 FROM vouchers WHERE client_id='고객D'").fetchone()
    con.close()
    assert (code, verdict) == (826, "recommended") and basis.startswith(BASIS_PREFIX)


def test_office_history_comes_before_claude_and_cuts_questions(tmp_path, enabled):
    from kafa.pipeline.ask import prepare_questions
    from kafa.pipeline.runner import run_pipeline
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _history(inbox)
    run_pipeline(inbox, out)
    _xlsx(inbox / "고객D" / "3월.xlsx", [_row("처음인쇄", "999-99-99999", 상태="미추천")])
    assert prepare_questions(inbox, out).new == 0          # 사무실 이력이 풀어 준다


def test_off_by_default(tmp_path):
    from kafa.pipeline.runner import run_pipeline
    assert office_prior.settings()["enabled"] is False
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _history(inbox)
    run_pipeline(inbox, out)
    _xlsx(inbox / "고객D" / "3월.xlsx", [_row("처음인쇄", "999-99-99999", 상태="미추천")])
    run_pipeline(inbox, out)
    con = sqlite3.connect(out / "kafa.db")
    code = con.execute("SELECT 차변계정코드 FROM vouchers WHERE client_id='고객D'").fetchone()[0]
    con.close()
    assert code is None


# ── 측정 ──

def test_check_prior_leave_one_client_out():
    recs = []
    for c in "ABCD":                                   # 인쇄업: 네 곳 다 826
        recs += [(c, f"인쇄{c}", f"1{c}", "제조", "인쇄", 826)] * 2
    for c in "ABCD":                                   # 한식: 갈림
        recs += [(c, f"식당{c}", f"2{c}", "음식점", "한식", 811 if c in "AB" else 813)]
    recs += [("A", "토스페이먼츠", "3A", "서비스", "광고", 833)] * 5   # 대행사 — 빠진다
    unresolved = [("E", "새인쇄", "제조", "인쇄"), ("E", "새식당", "음식점", "한식")]
    pc = check_prior(recs, unresolved, grid=((3, 0.8),))
    assert (pc.rows, pc.clients, pc.new_rows, pc.unresolved_rows) == (12, 4, 4, 2)
    (_, allc, alla, newc, newa, unres), = pc.lines
    assert (allc, alla) == ("67%", "100%")            # 인쇄 8건 채움·전부 정답, 한식은 안 채움
    assert (newc, newa) == ("0%", "-")                # 한 번만 나온 가맹점은 한식 4건뿐
    assert unres == 1
    text = pc.render({"min_clients": 3, "min_share": 0.8})
    assert "←지금" in text
    for name in ("인쇄A", "식당B", "토스페이먼츠", "새인쇄"):
        assert name not in text


def test_ask_cli_prior_prints_no_names(tmp_path, monkeypatch, capsys):
    from kafa import ask_cli
    from kafa.pipeline.runner import run_pipeline
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _history(inbox)
    run_pipeline(inbox, out)
    assert ask_cli.main(["prior", "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "한 수임처씩 빼고" in said and "꺼짐" in said
    assert "고객A" not in said and "인쇄소" not in said
