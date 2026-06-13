"""Phase 2 (골격) — 미추천 행 계정 추천 + 근거 + 신뢰도(+대안).

근거 우선순위:
  1) 시드(과거 처리분) 거래처별 최빈 계정 — 점유율을 신뢰도로.
  2) (시드 없음) 거래처명/적요 문자열 유사도 폴백 — 보수적 상한(config).
외부 LLM 호출 없음. 원천 데이터는 로컬 처리.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from kafa.config_loader import load_rules
from kafa.recommend.seed import SeedIndex
from kafa.rules.models import InputRow

_NON_ALNUM = re.compile(r"[^0-9A-Za-z가-힣]+")


def vendor_key(name: str) -> str:
    return _NON_ALNUM.sub("", (name or "")).lower()


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

    vk = vendor_key(row.거래처)

    # 1) 시드 빈도
    if seed is not None:
        hit = seed.top(vk)
        if hit is not None:
            code, share = hit
            return Recommendation(code, round(share, 3),
                                  basis=f"시드 거래처 최빈({share:.0%})", resolved=True)

    # 2) 유사도 폴백 (시드의 다른 거래처명과 비교)
    if seed is not None and seed.by_vendor:
        best_vk, best_score = None, 0.0
        for cand_vk in seed.by_vendor:
            s = SequenceMatcher(None, vk, cand_vk).ratio()
            if s > best_score:
                best_vk, best_score = cand_vk, s
        if best_vk and best_score >= floor:
            top = seed.top(best_vk)
            if top is not None:
                code, _ = top
                conf = round(min(best_score, cap), 3)
                return Recommendation(code, conf,
                                      basis=f"유사 거래처({best_score:.0%}) 폴백", resolved=True)

    # 3) 근거 없음 → 미해소
    return Recommendation(None, 0.0, basis="시드/유사 근거 없음 → 담당자 확인", resolved=False)
