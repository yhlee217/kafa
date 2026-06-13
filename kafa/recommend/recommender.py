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
