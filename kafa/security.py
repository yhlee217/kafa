"""보안 유틸 — PII 마스킹 (보안 제0원칙).

원천 세무데이터(사업자번호·카드번호·거래처 실명)는 어떤 LLM/에이전트 컨텍스트에도
올리지 않는다. 디스커버리·검증·리포트 노출 시 이 함수로 마스킹한다.
"""
from __future__ import annotations

import hashlib
import re


def mask_name(name: str, *, keep: int = 2) -> str:
    """거래처 실명 → 앞 keep 글자 + 마스크."""
    name = (name or "").strip()
    if not name:
        return ""
    if len(name) <= keep:
        return name[0] + "*" * (len(name) - 1) if len(name) > 1 else "*"
    return name[:keep] + "*" * (len(name) - keep)


def mask_bizno(bizno: str) -> str:
    """사업자번호 → 앞 3자리 + 마스크(예: 123-**-*****)."""
    digits = re.sub(r"\D", "", bizno or "")
    if not digits:
        return ""
    if len(digits) <= 3:
        return digits[:1] + "*" * (len(digits) - 1)
    return f"{digits[:3]}-**-*****"


def hash_id(value: str, *, salt: str = "kafa", length: int = 10) -> str:
    """안정적 익명 식별자(해시). 동일 입력 → 동일 토큰(중복키/매칭용)."""
    h = hashlib.sha256((salt + "|" + (value or "")).encode("utf-8")).hexdigest()
    return h[:length]
