"""1.7 계정명(한글) → 계정코드(숫자) 매핑. (제)/(판) 접두 처리 필수.

다운로드본 계정명은 (제)/(판) 접두로 제조원가/판관비를 구분한다.
  예) "(제)복리후생비"=511, "(판)복리후생비"=811
표기 흔들림((제)접두 vs (제)접미, 공백/괄호 변형)을 정규화해 매칭한다.
"""
from __future__ import annotations

import re
from typing import Optional

from kafa.config_loader import load_account_codes
from kafa.rules.models import RuleResult, Verdict

RULE_EXACT = "ACC-001"        # 정확 매칭
RULE_NORMALIZED = "ACC-002"   # 접두/접미 정규화 후 매칭
RULE_UNRESOLVED = "ACC-099"   # 매핑 실패 → 담당자 확인

# (제)/(판) 등 원가구분 마커. 접두/접미 어디 있든 추출.
_MARKER_RE = re.compile(r"[\(\[]\s*(제|판|도|관|분)\s*[\)\]]")
_SPACE_RE = re.compile(r"\s+")


def _strip_spaces(name: str) -> str:
    return _SPACE_RE.sub("", name.strip())


def _extract_marker(name: str) -> tuple[Optional[str], str]:
    """원가구분 마커와 마커 제거 후의 코어 계정명을 반환."""
    m = _MARKER_RE.search(name)
    marker = m.group(1) if m else None
    core = _MARKER_RE.sub("", name)
    return marker, _strip_spaces(core)


def _candidates(name: str) -> list[str]:
    """매핑 사전 조회용 후보 키들을 생성(우선순위 순)."""
    name = _strip_spaces(name)
    cands = [name]
    marker, core = _extract_marker(name)
    if marker:
        # 접두/접미 두 표기 모두 시도
        cands.append(f"({marker}){core}")
        cands.append(f"{core}({marker})")
    cands.append(core)  # 마커 무시 폴백
    # 중복 제거(순서 보존)
    seen: set[str] = set()
    out = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def map_account_name_to_code(
    name: str,
    *,
    config_dir: str | None = None,
) -> RuleResult:
    """계정명 → RuleResult(코드). 미매핑이면 unresolved."""
    if not name or not name.strip():
        return RuleResult(None, Verdict.UNRESOLVED, RULE_UNRESOLVED,
                          note="계정명 비어있음(미추천 가능)")

    mapping = load_account_codes(config_dir)
    # 1) 정규화 키로 사전 재구성(정확 매칭도 공백/표기 흔들림 흡수)
    norm_map = {_strip_spaces(k): v for k, v in mapping.items()}

    cands = _candidates(name)
    # 정확(원형) 우선
    raw = _strip_spaces(name)
    if raw in norm_map:
        return RuleResult(norm_map[raw], Verdict.RULE_CONFIRMED, RULE_EXACT)
    # 정규화 후보
    for cand in cands:
        key = _strip_spaces(cand)
        if key in norm_map:
            return RuleResult(norm_map[key], Verdict.RULE_CONFIRMED, RULE_NORMALIZED)

    return RuleResult(None, Verdict.UNRESOLVED, RULE_UNRESOLVED,
                      note=f"계정명 미매핑: {name!r} (계정과목 시트 파싱 또는 사전 보강 필요)")
