"""초안2 — 사업자등록번호 일괄 검증 + 홈택스 조회 대상 목록.

거래처 사업자번호를 건건이 홈택스에서 확인하던 작업을, 먼저 로컬에서 ① 중복 제거 ②
체크섬으로 '명백한 형식 오류'를 선별해 조회 건수를 줄인다. 실제 홈택스 상태조회(휴폐업·
유효성)는 API/공동인증 필요 → 보류. query_list 는 로컬 조회용(원문), 외부 노출은 마스킹.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from kafa.security import mask_bizno
from kafa.validate import valid_bizno

_DIGITS = re.compile(r"\D")


@dataclass
class BiznoBatch:
    total_input: int                                   # 비어있지 않은 입력 수
    unique: int                                        # 중복 제거 후
    duplicates: int                                    # 중복 건수
    query_list: list[str] = field(default_factory=list)    # 유효·중복제거(로컬 조회용, 원문)
    invalid_list: list[str] = field(default_factory=list)  # 체크섬/형식 오류(로컬, 원문)

    @property
    def n_valid(self) -> int:
        return len(self.query_list)

    @property
    def n_invalid(self) -> int:
        return len(self.invalid_list)


def check_biznos(biznos: list[str]) -> BiznoBatch:
    """사업자번호 목록을 정규화·중복제거하고 체크섬으로 유효/오류 분류."""
    seen: set[str] = set()
    uniq: list[str] = []
    total = 0
    for b in biznos:
        norm = _DIGITS.sub("", b or "")
        if not norm:
            continue
        total += 1
        if norm in seen:
            continue
        seen.add(norm)
        uniq.append(norm)
    valid = [b for b in uniq if valid_bizno(b)]
    invalid = [b for b in uniq if not valid_bizno(b)]
    return BiznoBatch(total_input=total, unique=len(uniq),
                      duplicates=total - len(uniq),
                      query_list=valid, invalid_list=invalid)


def render_bizno_summary(res: BiznoBatch) -> str:
    """요약(담당자용). 오류 목록은 마스킹해서 노출(원문은 query_list 로 로컬 보관)."""
    lines = [
        "── 사업자번호 일괄 검증 ──",
        f"입력 {res.total_input}건 → 중복 제거 {res.unique}건"
        f"(중복 {res.duplicates}건)",
        f"체크섬 유효 {res.n_valid}건(홈택스 조회 대상) / 형식 오류 의심 {res.n_invalid}건",
    ]
    if res.invalid_list:
        lines.append("  형식 오류 의심(마스킹):")
        for b in res.invalid_list:
            lines.append(f"    - {mask_bizno(b)}")
    lines.append("  ※ 휴폐업·실제 유효성은 홈택스 상태조회 필요(API/공동인증 — 보류).")
    return "\n".join(lines)
