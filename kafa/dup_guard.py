"""1.8 중복전표 2차 안전장치.

1차 방지는 위하고 '전표상태=중복전표' 컬럼을 신뢰(엔진에서 스킵).
2차로, 재업로드 사고 방지를 위해 처리한 키를 로컬에 보존한다.
키 = hash(거래일자 + 가맹점 + 사업자번호 + 합계 + 품명).

**품명을 키에 포함하는 이유**: 같은 날 같은 가맹점에서 같은 금액을 두 번 결제하는 일은
실제로 일어난다(커피 2회 등). 품명이 빠지면 그런 정상 거래가 중복으로 오인돼 업로드본에서
누락된다. 같은 파일을 재처리하는 경우엔 품명도 동일하므로 재업로드 방지 효과는 그대로다.
(여전히 품명·금액·날짜·가맹점이 모두 같은 별개 거래는 구분하지 못한다 — 1차 신호인
위하고 '전표상태=중복전표'가 우선이고, 이건 어디까지나 2차 안전장치다.)

키는 해시(원문 미보존)로 저장 → 로컬 파일에도 PII 평문을 남기지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from kafa.jsonstore import load_json, save_json
from kafa.security import hash_id


def make_key(거래일자: str, 가맹점: str, 사업자번호: str, 합계, 품명: str = "") -> str:
    raw = f"{거래일자}|{가맹점}|{사업자번호}|{합계}|{품명}"
    return hash_id(raw, salt="dupguard", length=16)


class DupGuard:
    """처리한 전표 키를 로컬 JSON 으로 보존하는 멱등 가드."""

    def __init__(self, store_path: str | Path):
        self.store_path = Path(store_path)
        loaded = load_json(self.store_path, [])
        self._seen: set[str] = set(loaded) if isinstance(loaded, list) else set()

    def is_duplicate(self, key: str) -> bool:
        return key in self._seen

    def record(self, key: str) -> None:
        self._seen.add(key)

    def flush(self) -> None:
        save_json(self.store_path, sorted(self._seen))
