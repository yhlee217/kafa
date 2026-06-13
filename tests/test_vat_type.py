"""1.1 과세유형 라벨↔코드 / 도출."""
import pytest

from kafa.rules.models import Deduct, Verdict
from kafa.rules.vat_type import (
    code_to_label,
    derive_vat_type,
    label_to_code,
    map_prefilled,
)


@pytest.mark.parametrize("label,code", [
    ("일반", 3), ("카과", 57), ("카면", 58), ("카영", 59), ("화물", 63),
])
def test_label_code_roundtrip(label, code):
    assert label_to_code(label) == code
    assert code_to_label(code) == label


def test_label_to_code_unknown():
    assert label_to_code("없는라벨") is None
    assert label_to_code("") is None


def test_map_prefilled_ok():
    r = map_prefilled("카과")
    assert r.value == 57
    assert r.verdict == Verdict.RULE_CONFIRMED


def test_map_prefilled_unknown_unresolved():
    r = map_prefilled("이상한값")
    assert r.verdict == Verdict.UNRESOLVED
    assert r.value is None


@pytest.mark.parametrize("taxation,deduct,expected", [
    ("과세", Deduct.DEDUCTIBLE, 57),       # 과세+공제 → 카과
    ("면세", Deduct.DEDUCTIBLE, 58),       # 면세+공제 → 카면
    ("과세", Deduct.NON_DEDUCTIBLE, 3),    # 불공제 → 일반
    ("면세", Deduct.NON_DEDUCTIBLE, 3),    # 불공제는 면세여도 일반
])
def test_derive_vat_type(taxation, deduct, expected):
    r = derive_vat_type(taxation, deduct)
    assert r.value == expected
    assert r.verdict == Verdict.RULE_CONFIRMED
