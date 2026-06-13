"""사업자번호 체크섬 / 부가율 이상 검증."""
from decimal import Decimal

from kafa.validate import effective_vat_rate, valid_bizno, vat_rate_anomaly


def test_valid_bizno_true():
    # 합성 유효번호(체크섬 만족): 111-11-11119
    assert valid_bizno("111-11-11119") is True
    assert valid_bizno("1111111119") is True


def test_valid_bizno_false():
    assert valid_bizno("111-11-11111") is False   # 잘못된 검증숫자
    assert valid_bizno("123-45-67890") is False
    assert valid_bizno("") is False
    assert valid_bizno("12345") is False           # 자릿수 부족
    assert valid_bizno("11111111a9") is False       # 숫자 아님


def test_effective_vat_rate():
    assert effective_vat_rate(10000, 1000) == Decimal("0.1")
    assert effective_vat_rate(0, 1000) is None


def test_vat_rate_anomaly_normal_is_none():
    assert vat_rate_anomaly(10000, 1000) is None    # 정확히 10%


def test_vat_rate_anomaly_deviation():
    assert vat_rate_anomaly(10000, 500) is not None  # 5% → 이상


def test_vat_rate_anomaly_zero_tax_is_none():
    assert vat_rate_anomaly(10000, 0) is None        # 면세/비과세


def test_vat_rate_anomaly_supply_zero_with_tax():
    msg = vat_rate_anomaly(0, 100)
    assert msg is not None and "공급가액" in msg
