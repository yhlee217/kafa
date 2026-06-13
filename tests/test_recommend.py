"""Phase 2 — 자가 시딩 + 미추천 추천."""
from decimal import Decimal

from kafa.recommend.recommender import recommend_account
from kafa.recommend.seed import SeedIndex, build_seed_from_inputrows
from kafa.rules.models import InputRow


def _row(거래처, 사업자번호="", 차변계정="", 전표상태=""):
    return InputRow(연도="2026", 일자="03-15", 거래처=거래처,
                    사업자등록번호=사업자번호, 차변계정=차변계정,
                    전표상태=전표상태, 합계=Decimal("1000"))


def test_self_seed_recommends_same_vendor():
    rows = [
        _row("스타벅스", "111-11-11111", 차변계정="(판)복리후생비"),
        _row("스타벅스", "111-11-11111", 차변계정="(판)복리후생비"),
        _row("스타벅스", "111-11-11111", 전표상태="미추천"),  # 미추천 — 시드 제외
    ]
    seed = build_seed_from_inputrows(rows)
    rec = recommend_account(rows[-1], seed)
    assert rec.resolved is True
    assert rec.account_code == 811
    assert rec.confidence == 1.0


def test_bizno_takes_priority():
    seed = SeedIndex()
    # 같은 사업자번호엔 822, 다른(동명이인) 거래처명엔 811
    seed.add("가맹점", "222-22-22222", 822)
    seed.add("가맹점", "999-99-99999", 811)
    seed.add("가맹점", "999-99-99999", 811)
    rec = recommend_account(_row("가맹점", "222-22-22222"), seed)
    assert rec.account_code == 822
    assert "사업자번호" in rec.basis


def test_empty_seed_unresolved():
    rec = recommend_account(_row("미정가맹점"), SeedIndex())
    assert rec.resolved is False
    assert rec.account_code is None


def test_unknown_account_name_not_seeded():
    rows = [_row("가게", "333-33-33333", 차변계정="존재하지않는계정")]
    seed = build_seed_from_inputrows(rows)
    assert seed.empty
