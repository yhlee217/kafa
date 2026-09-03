"""결제대행사 식별 — 카드 전표의 거래처가 '실제 가맹점' 이 아닌 경우.

카드 매입 내역에는 실제 가맹점 대신 **결제대행사(PG)** 나 교통 정산사업자,
카드사가 찍히는 경우가 있다. 그 이름만으로는 무엇을 샀는지 알 수 없어 계정을
정할 수 없다(담당자 확인 2026-09-03). 실측: 전체의 21.3%가 여기 해당한다.

같은 대행사가 표기만 달리 나오므로(전각 괄호·법인 표기·띄어쓰기) 정규화 후 비교한다.
목록은 `config/rules.yaml` 의 `payment_agents` 에 있다 — 코드가 아니라 거기서 고친다.
"""
from __future__ import annotations

import re
from functools import lru_cache

from kafa.config_loader import load_rules

# 이름 비교 전에 지우는 표기(같은 회사가 여러 모양으로 찍힌다)
_NOISE = ("주식회사", "(주)", "（주）", "㈜", "(사단법인)", "（사단법인）",
          "유한회사", "(유)", "（유）", "(합)", "(재)", "(사)")


def normalize(name: str) -> str:
    """거래처 이름 비교용 정규화 — 법인 표기·공백·구두점을 지운다."""
    text = name or ""
    for word in _NOISE:
        text = text.replace(word, "")
    return re.sub(r"[\s·.,\-_'\"]+", "", text)


@lru_cache(maxsize=None)
def _index(config_dir: str | None) -> tuple[tuple[str, str, str], ...]:
    """(정규화 키워드, 갈래, 표시 이름) 목록. 긴 키워드부터 본다."""
    cfg = (load_rules(config_dir).get("payment_agents") or {})
    out = []
    for group, spec in (cfg.get("groups") or {}).items():
        for keyword in (spec.get("keywords") or []):
            key = normalize(str(keyword))
            if key:
                out.append((key, group, str(keyword)))
    out.sort(key=lambda x: -len(x[0]))
    return tuple(out)


def agent_of(거래처: str, *, config_dir: str | None = None):
    """이 거래처가 대행사면 (갈래, 표시 이름), 아니면 None."""
    name = normalize(거래처)
    if not name:
        return None
    for key, group, label in _index(config_dir):
        if key in name:
            return group, label
    return None


def agent_note(group: str, label: str, *, config_dir: str | None = None) -> str:
    """담당자가 읽을 사유 문장."""
    cfg = (load_rules(config_dir).get("payment_agents") or {})
    tail = ((cfg.get("groups") or {}).get(group) or {}).get("note") or \
        "실제 가맹점을 알 수 없습니다"
    return f"{label} — {tail}"
