"""보안 마스킹 + 중복 가드(2차)."""
from kafa.dup_guard import DupGuard, make_key
from kafa.security import hash_id, mask_bizno, mask_name


def test_mask_name():
    assert mask_name("스타벅스코리아") == "스타*****"   # 앞 2글자 + 마스크
    assert mask_name("") == ""
    assert "*" in mask_name("우리")


def test_mask_bizno():
    assert mask_bizno("123-45-67890") == "123-**-*****"
    assert mask_bizno("") == ""


def test_hash_id_stable():
    assert hash_id("abc") == hash_id("abc")
    assert hash_id("abc") != hash_id("abd")


def test_dup_guard(tmp_path):
    store = tmp_path / "dup.json"
    g = DupGuard(store)
    k = make_key("2026-03-15", "가맹점", "000-00-00000", "11000")
    assert not g.is_duplicate(k)
    g.record(k)
    g.flush()
    # 재로드 시 유지
    g2 = DupGuard(store)
    assert g2.is_duplicate(k)
