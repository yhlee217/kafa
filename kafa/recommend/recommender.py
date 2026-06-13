"""Phase 2 — 미추천 행 계정 추천 + 근거 + 신뢰도(+대안).

근거 우선순위:
  1) 시드 사업자번호 정확 — 같은 사업자번호의 최빈 계정(가장 신뢰).
  2) 시드 거래처명 정확 — 정규화 거래처명 최빈 계정.
  3) (정확 없음) 거래처명 문자열 유사도 폴백 — 보수적 상한(config).
점유율(빈도 비율)을 신뢰도로 쓰되 유사도 폴백은 상한 적용.
외부 LLM 호출 없음. 원천 데이터는 로컬 처리.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from kafa.config_loader import load_rules
from kafa.recommend.seed import SeedIndex, vendor_key
from kafa.rules.models import InputRow


@dataclass
class Recommendation:
    account_code: Optional[int]
    confidence: float
    basis: str                       # 근거 설명
    alternatives: list[int] = field(default_factory=list)
    resolved: bool = False           # 추천 채택 가능 여부


def recommend_account(
    row: InputRow,
    seed: Optional[SeedIndex] = None,
    *,
    config_dir: str | None = None,
) -> Recommendation:
    cfg = load_rules(config_dir).get("recommend", {}) or {}
    cap = float(cfg.get("similarity_confidence_cap", 0.60))
    floor = float(cfg.get("similarity_floor", 0.80))

    if seed is None or seed.empty:
        return Recommendation(None, 0.0, "시드 없음 → 담당자 확인", resolved=False)

    # 1) 사업자번호 정확
    hit = seed.top_by_bizno(row.사업자등록번호)
    if hit is not None:
        code, share = hit
        return Recommendation(code, round(share, 3),
                              f"시드 사업자번호 최빈({share:.0%})", resolved=True)

    # 2) 거래처명 정확
    hit = seed.top_by_vendor(row.거래처)
    if hit is not None:
        code, share = hit
        return Recommendation(code, round(share, 3),
                              f"시드 거래처 최빈({share:.0%})", resolved=True)

    # 3) 거래처명 유사도 폴백
    vk = vendor_key(row.거래처)
    best_vk, best_score = None, 0.0
    for cand_vk in seed.by_vendor:
        s = SequenceMatcher(None, vk, cand_vk).ratio()
        if s > best_score:
            best_vk, best_score = cand_vk, s
    if best_vk and best_score >= floor:
        top = seed._top(seed.by_vendor[best_vk])
        if top is not None:
            code, _ = top
            return Recommendation(code, round(min(best_score, cap), 3),
                                  f"유사 거래처({best_score:.0%}) 폴백", resolved=True)

    return Recommendation(None, 0.0, "근거 없음 → 담당자 확인", resolved=False)


# ── 통합 추천기 인터페이스 ────────────────────────────────────────────
# 미추천 행 → Recommendation. LLM(비-PII 특징) 또는 시드/유사도(로컬, PII 로컬 처리).

class Recommender:
    """공통 인터페이스: 입력 행 1건 → Recommendation."""

    def recommend_for(self, row: InputRow) -> Recommendation:  # pragma: no cover
        raise NotImplementedError


class SeedRecommender(Recommender):
    """결정론 폴백 — 시드 빈도/유사도(로컬). LLM 미사용 환경에서."""

    def __init__(self, seed: Optional[SeedIndex] = None, *, config_dir=None):
        self.seed = seed
        self.config_dir = config_dir

    def recommend_for(self, row: InputRow) -> Recommendation:
        return recommend_account(row, self.seed, config_dir=self.config_dir)


class PickRecommender(Recommender):
    """호스트 Claude(Desktop/Code)가 정한 계정을 적용. 별도 API 키·토큰 청구 없음.

    picks: {row.raw_index: {"account_code": int, "confidence"?: float, "rationale"?: str}}.
    허용 계정코드만 채택(환각/오류 방어), 미매칭/미허용은 fallback(시드).
    """

    def __init__(self, picks: dict, allowed_codes: set[int],
                 fallback: Optional[Recommender] = None):
        self.picks = {int(k): v for k, v in (picks or {}).items()}
        self.allowed = set(allowed_codes)
        self.fallback = fallback

    def recommend_for(self, row: InputRow) -> Recommendation:
        p = self.picks.get(row.raw_index)
        if p is not None:
            code = p.get("account_code")
            try:
                code = int(code) if code is not None else None
            except (TypeError, ValueError):
                code = None
            if code in self.allowed:
                conf = p.get("confidence")
                conf = float(conf) if conf is not None else 0.9
                basis = "AI(Claude) 추정: " + str(p.get("rationale") or "").strip()
                return Recommendation(code, round(max(0.0, min(1.0, conf)), 3),
                                      basis, resolved=True)
        if self.fallback is not None:
            return self.fallback.recommend_for(row)
        return Recommendation(None, 0.0, "추천 없음 → 담당자 확인", resolved=False)


class LLMRecommenderAdapter(Recommender):
    """LLM 추정 — 비-PII 특징만 전달. 실패 시 시드로 폴백."""

    def __init__(self, llm, allowed: dict[str, int],
                 fallback: Optional[Recommender] = None, *, config_dir=None):
        self.llm = llm
        self.allowed = allowed
        self.fallback = fallback
        self.config_dir = config_dir

    def recommend_for(self, row: InputRow) -> Recommendation:
        from kafa.recommend.features import account_features
        try:
            rec = self.llm.recommend(account_features(row), self.allowed)
        except Exception as e:  # noqa: BLE001 — LLM 실패가 배치를 막지 않음
            if self.fallback is not None:
                fb = self.fallback.recommend_for(row)
                fb.basis = f"LLM 실패({type(e).__name__}) → " + fb.basis
                return fb
            return Recommendation(None, 0.0, f"LLM 실패: {type(e).__name__}",
                                  resolved=False)
        if not rec.resolved and self.fallback is not None:
            return self.fallback.recommend_for(row)
        return rec


def build_recommender(seed: Optional[SeedIndex] = None, *,
                      config_dir: str | None = None) -> Recommender:
    """config 와 환경(API 키)에 따라 추천기 구성.

    recommend.llm.enabled 이고 API 키가 있으면 LLM 추정(폴백=시드), 아니면 시드.
    """
    seed_reco = SeedRecommender(seed, config_dir=config_dir)
    cfg = (load_rules(config_dir).get("recommend", {}) or {}).get("llm", {}) or {}
    if not cfg.get("enabled"):
        return seed_reco

    from kafa.recommend.llm import AnthropicRecommender, allowed_accounts, llm_available
    if not llm_available():
        return seed_reco           # API 키 없음 → 로컬 폴백(오프라인/CI 안전)
    try:
        llm = AnthropicRecommender(config_dir=config_dir)
    except Exception:              # noqa: BLE001 — SDK 미설치 등
        return seed_reco
    return LLMRecommenderAdapter(llm, allowed_accounts(config_dir),
                                 fallback=seed_reco, config_dir=config_dir)
