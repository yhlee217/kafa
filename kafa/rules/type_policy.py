"""유형(카과/일반/카면) 기준 — 담당자가 말한 기준(계정 성격 × 수임처 속성 × 세액 유무).

받은 자료의 유형은 위하고 기본값(≈ 국세청)이라 담당자 판단이 아니다. 담당자가 확정한 행은 93%
카과였다(2026-11-26 실측). 기준표는 config `type_policy` 에 두고, 담당자 확인 전에는 끈다.
근거·확인 경과: docs/domain_notes.md, docs/decisions.md.

담당자가 정한 유형은 건드리지 않는다 — 위하고 '전표확정' 행, kafa-desk 답지 행, 같은 가맹점의
담당자 이력으로 유형을 정한 행. 봉사료·의심 업종·자동 불공제처럼 앞단 안전장치가 정한 공제 여부도 그대로.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from kafa.config_loader import load_account_codes, load_rules
from kafa.recommend.type_seed import RULE_TYPE_HIST
from kafa.rules.models import Deduct

RULE_TYPE_POLICY = "TYPE-POLICY"
_GUARDS = ("DED-001", "DED-003", "DED-005")      # 자동 불공제·의심(검토)·봉사료 — 사람/법이 정함


@dataclass
class TypeDecision:
    label: str
    why: str


def policy(config_dir: str | None = None) -> dict:
    return load_rules(config_dir).get("type_policy") or {}


@lru_cache(maxsize=None)
def _names_by_code(config_dir: str | None) -> dict[int, tuple[str, ...]]:
    out: dict[int, list[str]] = {}
    for name, code in load_account_codes(config_dir).items():
        out.setdefault(int(code), []).append(str(name))
    return {c: tuple(ns) for c, ns in out.items()}


def _hit(names: tuple[str, ...], words) -> str | None:
    for w in words or []:
        if w and any(str(w) in n for n in names):
            return str(w)
    return None


def _when(profile: dict, cond) -> bool:
    if not cond:
        return True
    if cond == "has_employees":
        return bool(profile.get("has_employees", True))
    return bool(profile.get(str(cond), False))


def has_vat(amount) -> bool:
    try:
        return Decimal(str(amount or 0)) != 0
    except (InvalidOperation, ValueError):
        return False


def decide_type(code: int | None, vat: bool, profile: dict, pol: dict,
                config_dir: str | None = None) -> TypeDecision | None:
    """기준표로 정한 유형. 기준표에 없으면 None(위하고 값을 그대로 둔다)."""
    names = _names_by_code(config_dir).get(int(code), ()) if code is not None else ()
    if not vat:
        ex = pol.get("no_vat_exempt") or {}
        word = _hit(names, ex.get("accounts"))
        if word and ex.get("type") and _when(profile, ex.get("when")):
            return TypeDecision(str(ex["type"]), f"세액 없음 · {word}(면세 재료)")
        label = pol.get("no_vat_type")
        return TypeDecision(str(label), "세액 없음") if label else None
    for rule in pol.get("rules") or []:
        word = _hit(names, rule.get("accounts"))
        if not word:
            continue
        if _when(profile, rule.get("when")):
            return TypeDecision(str(rule["type"]), f"{word} · 세액 있음")
        other = rule.get("otherwise")
        return TypeDecision(str(other), f"{word} · {rule.get('when')} 아님") if other else None
    return None


def apply_type_policy(c, profile: dict | None, *, config_dir: str | None = None) -> bool:
    """기준표가 켜져 있으면 유형·공제여부를 맞춘다. 바꿨으면 True."""
    from kafa.rules.vat_type import label_to_code
    from kafa.store.db import status_settings

    pol = policy(config_dir)
    if not pol.get("enabled") or c.skipped or c.source is None:
        return False
    confirmed, desk = status_settings(config_dir)
    if (c.source.전표상태 or "").strip() in confirmed | {desk}:
        return False
    if RULE_TYPE_HIST in c.판정근거 or any(g in c.판정근거 for g in _GUARDS):
        return False
    code = c.대변계정코드 if c.reversal_applied else c.차변계정코드
    d = decide_type(code, has_vat(c.source.세액), profile or {}, pol, config_dir)
    if d is None:
        return False
    new = label_to_code(d.label, config_dir=config_dir)
    if new is None or new == c.유형코드:
        return False
    c.유형코드 = new
    by_type = (load_rules(config_dir).get("non_deductible") or {}).get("by_type") or {}
    decided = str(by_type.get(d.label, "")).strip()
    if decided == "공제":
        c.공제여부 = Deduct.DEDUCTIBLE
    elif decided == "불공제":
        c.공제여부 = Deduct.NON_DEDUCTIBLE
    c.add_rule(RULE_TYPE_POLICY)
    note = f"유형 {d.label}: 담당자 기준({d.why})"
    c.추천근거 = f"{c.추천근거} · {note}" if c.추천근거 else note
    return True
