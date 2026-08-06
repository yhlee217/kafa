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


# ── 업종 최빈 폴백(수임처별 기준 학습) ──

def _ind_seed(pairs):
    """pairs = [(계정코드, 건수)] → 음식점업|한식 업종 시드."""
    s = SeedIndex()
    for code, n in pairs:
        for i in range(n):
            s.add(f"가맹{code}{i}", "", code, "음식점업", "한식")
    return s


def test_industry_fallback_resolves_unknown_vendor():
    # 처음 보는 가맹점이지만 이 수임처는 음식점을 접대비(813)로 처리해왔음
    row = InputRow(거래처="처음보는식당", 업태="음식점업", 종목="한식")
    rec = recommend_account(row, _ind_seed([(813, 4)]))
    assert rec.resolved and rec.account_code == 813
    assert rec.confidence <= 0.55 and "업종 최빈" in rec.basis


def test_industry_fallback_declines_on_tie():
    # 반반으로 갈리는 업종은 추천하지 않고 담당자 확인으로
    row = InputRow(거래처="처음보는식당", 업태="음식점업", 종목="한식")
    rec = recommend_account(row, _ind_seed([(813, 3), (811, 3)]))
    assert not rec.resolved and rec.account_code is None


def test_industry_fallback_needs_min_support():
    # 근거 2건뿐이면(기본 min_support=3) 추천하지 않음
    row = InputRow(거래처="처음보는식당", 업태="음식점업", 종목="한식")
    assert not recommend_account(row, _ind_seed([(813, 2)])).resolved


def test_vendor_seed_beats_industry():
    # 가맹점 근거가 있으면 업종보다 우선(더 구체적)
    s = _ind_seed([(813, 5)])
    s.add("단골식당", "", 811, "음식점업", "한식")
    row = InputRow(거래처="단골식당", 업태="음식점업", 종목="한식")
    rec = recommend_account(row, s)
    assert rec.account_code == 811 and "거래처" in rec.basis
