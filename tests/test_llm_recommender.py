"""LLM 기반 차변계정 추정 — 주입형 가짜 client (실제 API 호출 없음, 합성 데이터)."""
import json
from decimal import Decimal
from types import SimpleNamespace

from kafa.recommend.features import account_features, feature_signature
from kafa.recommend.llm import AnthropicRecommender, allowed_accounts, llm_available
from kafa.recommend.recommender import LLMRecommenderAdapter, SeedRecommender, build_recommender
from kafa.rules.models import InputRow


class FakeClient:
    """messages.create 를 흉내내고 마지막 페이로드를 기록."""
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls = 0
        self.last_kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        text = json.dumps(self._payload, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _row(거래처="고유상호명칭", bizno="111-11-11119", 업태="음식점", 종목="카페",
         품명="커피", 유형="카과"):
    return InputRow(연도="2026", 일자="03-15", 거래처=거래처, 사업자등록번호=bizno,
                    업태=업태, 종목=종목, 품명=품명, 유형=유형, 전표상태="미추천",
                    합계=Decimal("5500"))


def test_account_features_excludes_pii():
    feats = account_features(_row())
    assert set(feats) == {"업태", "종목", "품명", "유형"}
    assert "거래처" not in feats and "사업자등록번호" not in feats


def test_feature_signature_stable():
    a = feature_signature(account_features(_row()))
    b = feature_signature(account_features(_row()))
    assert a == b


def test_llm_maps_name_to_code_and_clamps_confidence():
    fake = FakeClient({"account_name": "(판)복리후생비", "confidence": 1.4,
                       "rationale": "음식점 간식 매입", "alternatives": ["(판)차량유지비"]})
    rec = AnthropicRecommender(client=fake).recommend(
        account_features(_row()), allowed_accounts())
    assert rec.resolved and rec.account_code == 811
    assert rec.confidence == 1.0                # 1.4 → 1.0 클램프
    assert rec.basis.startswith("LLM 추정:")
    assert 822 in rec.alternatives


def test_llm_rejects_disallowed_account():
    fake = FakeClient({"account_name": "존재하지않는계정", "confidence": 0.9,
                       "rationale": "x"})
    rec = AnthropicRecommender(client=fake).recommend(
        account_features(_row()), allowed_accounts())
    assert rec.resolved is False and rec.account_code is None


def test_decision_cache_dedups_calls():
    fake = FakeClient({"account_name": "(판)복리후생비", "confidence": 0.9,
                       "rationale": "x"})
    reco = AnthropicRecommender(client=fake)
    feats = account_features(_row())
    reco.recommend(feats, allowed_accounts())
    reco.recommend(feats, allowed_accounts())
    assert fake.calls == 1                       # 동일 특징 → 캐시 재사용(재현성)


def test_no_pii_in_llm_payload():
    fake = FakeClient({"account_name": "(판)복리후생비", "confidence": 0.9,
                       "rationale": "x"})
    adapter = LLMRecommenderAdapter(AnthropicRecommender(client=fake),
                                    allowed_accounts(), fallback=None)
    adapter.recommend_for(_row(거래처="고유상호명칭", bizno="111-11-11119"))
    blob = json.dumps(fake.last_kwargs, ensure_ascii=False, default=str)
    assert "고유상호명칭" not in blob            # 거래처 실명 미전송
    assert "111-11-11119" not in blob and "1111111119" not in blob  # 사업자번호 미전송


def test_persistent_cache_roundtrip(tmp_path):
    p = tmp_path / "llm_cache.json"
    fake = FakeClient({"account_name": "(판)차량유지비", "confidence": 0.8,
                       "rationale": "주유"})
    feats = account_features(_row(업태="주유소", 종목="경유", 품명="경유"))
    AnthropicRecommender(client=fake, cache_path=str(p)).recommend(feats, allowed_accounts())
    assert p.exists()
    # 새 인스턴스(다른 fake, 호출 시 에러) → 캐시에서 재현
    fake2 = FakeClient({})
    rec = AnthropicRecommender(client=fake2, cache_path=str(p)).recommend(feats, allowed_accounts())
    assert rec.account_code == 822 and fake2.calls == 0


def test_adapter_falls_back_when_unresolved():
    fake = FakeClient({"account_name": "없는계정", "confidence": 0.1, "rationale": "x"})
    seed_called = {"n": 0}

    class FB(SeedRecommender):
        def recommend_for(self, row):
            seed_called["n"] += 1
            from kafa.recommend.recommender import Recommendation
            return Recommendation(811, 0.5, "시드 폴백", resolved=True)

    adapter = LLMRecommenderAdapter(AnthropicRecommender(client=fake),
                                    allowed_accounts(), fallback=FB())
    rec = adapter.recommend_for(_row())
    assert rec.account_code == 811 and seed_called["n"] == 1


def test_build_recommender_without_key_is_seed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert llm_available() is False
    assert isinstance(build_recommender(None), SeedRecommender)
