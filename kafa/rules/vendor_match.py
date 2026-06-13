"""거래처명 ↔ 마스터 매칭: 정확 → 후보 → 미매칭(unresolved).

정확   : 사업자번호 일치(또는 정규화 거래처명 완전일치) → matched.
후보   : 이름 유사도 임계 이상 → candidate (담당자 확인).
미매칭 : 임계 미만 → unresolved.

마스터는 선택. 없으면(빈 마스터) 항상 unresolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from kafa.rules.models import Verdict

RULE_EXACT_BIZNO = "VM-001"
RULE_EXACT_NAME = "VM-002"
RULE_CANDIDATE = "VM-003"
RULE_UNMATCHED = "VM-099"

_NON_ALNUM = re.compile(r"[^0-9A-Za-z가-힣]+")


def _norm_name(name: str) -> str:
    return _NON_ALNUM.sub("", (name or "")).lower()


def _norm_bizno(bizno: str) -> str:
    return re.sub(r"\D", "", bizno or "")


@dataclass
class VendorMaster:
    """거래처 마스터. by_bizno: 사업자번호→표준 거래처명. names: 표준 거래처명 목록."""
    by_bizno: dict[str, str]
    names: list[str]

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str]]) -> "VendorMaster":
        by_bizno = {_norm_bizno(b): n for b, n in pairs if b}
        names = [n for _, n in pairs]
        return cls(by_bizno=by_bizno, names=names)


@dataclass
class MatchResult:
    status: Verdict          # RULE_CONFIRMED(정확) | REVIEW(후보) | UNRESOLVED
    matched_name: Optional[str]
    rule_id: str
    score: float = 0.0
    candidate: Optional[str] = None


def match_vendor(
    name: str,
    bizno: str = "",
    master: Optional[VendorMaster] = None,
    *,
    similarity_floor: float = 0.80,
) -> MatchResult:
    if master is None or (not master.by_bizno and not master.names):
        return MatchResult(Verdict.UNRESOLVED, None, RULE_UNMATCHED,
                           score=0.0)

    # 1) 사업자번호 정확 매칭
    nb = _norm_bizno(bizno)
    if nb and nb in master.by_bizno:
        return MatchResult(Verdict.RULE_CONFIRMED, master.by_bizno[nb],
                           RULE_EXACT_BIZNO, score=1.0)

    # 2) 정규화 이름 완전일치
    target = _norm_name(name)
    norm_to_orig = {_norm_name(n): n for n in master.names}
    if target and target in norm_to_orig:
        return MatchResult(Verdict.RULE_CONFIRMED, norm_to_orig[target],
                           RULE_EXACT_NAME, score=1.0)

    # 3) 유사도 후보
    best_name, best_score = None, 0.0
    for cand in master.names:
        s = SequenceMatcher(None, target, _norm_name(cand)).ratio()
        if s > best_score:
            best_name, best_score = cand, s

    if best_name and best_score >= similarity_floor:
        return MatchResult(Verdict.REVIEW, None, RULE_CANDIDATE,
                           score=best_score, candidate=best_name)

    return MatchResult(Verdict.UNRESOLVED, None, RULE_UNMATCHED, score=best_score)
