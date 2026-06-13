"""1.7 계정명→코드 매핑 ((제)/(판) 접두 처리)."""
import pytest

from kafa.rules.accounts import map_account_name_to_code
from kafa.rules.models import Verdict


@pytest.mark.parametrize("name,code", [
    ("미지급비용", 262),
    ("미지급금", 253),
    ("외상매입금", 251),
    ("선급금", 131),
    ("현금", 101),
    ("가지급금", 134),
    ("(제)복리후생비", 511),
    ("(판)복리후생비", 811),
    ("(판)차량유지비", 822),
])
def test_exact_and_prefix(name, code):
    r = map_account_name_to_code(name)
    assert r.value == code
    assert r.verdict in (Verdict.RULE_CONFIRMED,)


def test_suffix_notation_normalized():
    # 접미 표기도 정규화되어 매칭
    assert map_account_name_to_code("복리후생비(제)").value == 511
    assert map_account_name_to_code("차량유지비(판)").value == 822


def test_whitespace_tolerant():
    assert map_account_name_to_code(" (판) 차량유지비 ").value == 822


def test_empty_unresolved():
    r = map_account_name_to_code("")
    assert r.verdict == Verdict.UNRESOLVED
    assert r.value is None


def test_unknown_unresolved():
    r = map_account_name_to_code("존재하지않는계정")
    assert r.verdict == Verdict.UNRESOLVED
    assert r.value is None
