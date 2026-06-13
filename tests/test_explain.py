"""근거 생성 — 규칙ID + 맥락 → 한국어 근거."""
from decimal import Decimal

from kafa.recommend.explain import explain
from kafa.rules.models import ClassifiedRow, InputRow, Verdict


def _src(**kw):
    base = dict(연도="2026", 일자="03-15", 거래처="가맹점", 국세청="공제",
                유형="카과", 차변계정="(판)복리후생비", 합계=Decimal("11000"))
    base.update(kw)
    return InputRow(**base)


def test_explain_rule_confirmed():
    r = ClassifiedRow(유형코드=57, 차변계정코드=811, 대변계정코드=262,
                      판정근거=["DED-002", "VAT-001", "ACC-001", "CP-001"],
                      source=_src())
    text = explain(r)
    assert "국세청" in text
    assert "미지급비용" in text
    assert "코드 매핑" in text


def test_explain_recommended_includes_basis():
    r = ClassifiedRow(차변계정코드=811, 판정유형=Verdict.RECOMMENDED,
                      추천근거="LLM 추정: 카페 커피 매입 → 복리후생비",
                      판정근거=["ACC-PENDING", "RECO-001"], source=_src(차변계정=""))
    text = explain(r)
    assert "추천 대상" in text
    assert "LLM 추정" in text


def test_explain_skip():
    r = ClassifiedRow(skipped=True, skip_reason="중복전표",
                      판정근거=["SKIP-001"], source=_src())
    assert "스킵" in explain(r)


def test_explain_handles_missing_source():
    r = ClassifiedRow(판정근거=["VAT-001"], source=None)
    assert explain(r) == ""           # source 없으면 안전하게 빈 문자열
