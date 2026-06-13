"""거래처 ↔ 마스터 매칭: 정확→후보→미매칭."""
from kafa.rules.models import Verdict
from kafa.rules.vendor_match import VendorMaster, match_vendor


def _master():
    return VendorMaster.from_pairs([
        ("123-45-67890", "스타벅스코리아"),
        ("221-88-11111", "지에스칼텍스주유소"),
    ])


def test_exact_by_bizno():
    r = match_vendor("아무이름", "1234567890", _master())
    assert r.status == Verdict.RULE_CONFIRMED
    assert r.matched_name == "스타벅스코리아"
    assert r.rule_id == "VM-001"


def test_exact_by_name():
    r = match_vendor("스타벅스코리아", "", _master())
    assert r.status == Verdict.RULE_CONFIRMED
    assert r.rule_id == "VM-002"


def test_candidate_by_similarity():
    r = match_vendor("스타벅스 코리아(주)", "", _master())
    assert r.status == Verdict.REVIEW
    assert r.candidate == "스타벅스코리아"


def test_unmatched():
    r = match_vendor("전혀다른상호xyz", "", _master())
    assert r.status == Verdict.UNRESOLVED


def test_no_master_unresolved():
    r = match_vendor("스타벅스코리아", "1234567890", None)
    assert r.status == Verdict.UNRESOLVED
