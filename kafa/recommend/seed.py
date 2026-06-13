"""Phase 2 시드 — (거래처/사업자번호 → 계정코드) 빈도 인덱스.

시드 출처 우선순위:
  1) 위하고 과거 처리분 전표 엑셀 (확보 시) — build_seed_index.
  2) **같은 다운로드 배치 안에서 위하고가 이미 채운 행** (차변계정 보유)
     → build_seed_from_inputrows. 외부 데이터 없이 자가 시딩.
원천 데이터는 로컬 코드에서만 처리(보안 제0원칙).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from kafa.rules.accounts import map_account_name_to_code

_NON_ALNUM = re.compile(r"[^0-9A-Za-z가-힣]+")
_DIGITS = re.compile(r"\D")


def vendor_key(name: str) -> str:
    return _NON_ALNUM.sub("", (name or "")).lower()


def bizno_key(bizno: str) -> str:
    return _DIGITS.sub("", bizno or "")


@dataclass
class SeedIndex:
    """거래처(정규화)/사업자번호 → 계정코드 빈도."""
    by_vendor: dict[str, Counter] = field(default_factory=dict)
    by_bizno: dict[str, Counter] = field(default_factory=dict)

    def add(self, vendor: str, bizno: str, account_code: int) -> None:
        vk = vendor_key(vendor)
        if vk:
            self.by_vendor.setdefault(vk, Counter())[account_code] += 1
        bk = bizno_key(bizno)
        if bk:
            self.by_bizno.setdefault(bk, Counter())[account_code] += 1

    @staticmethod
    def _top(counter: Counter | None) -> tuple[int, float] | None:
        if not counter:
            return None
        code, n = counter.most_common(1)[0]
        return code, n / sum(counter.values())

    def top_by_bizno(self, bizno: str) -> tuple[int, float] | None:
        return self._top(self.by_bizno.get(bizno_key(bizno)))

    def top_by_vendor(self, vendor: str) -> tuple[int, float] | None:
        return self._top(self.by_vendor.get(vendor_key(vendor)))

    @property
    def empty(self) -> bool:
        return not self.by_vendor and not self.by_bizno


def build_seed_index(records: Iterable[tuple[str, str, int]]) -> SeedIndex:
    """과거 처리분 레코드[(거래처, 사업자번호, 계정코드), ...] → SeedIndex.

    TODO: 위하고 과거 전표 엑셀 리더와 연결. 최근성 가중 추가.
    """
    idx = SeedIndex()
    for vendor, bizno, code in records or []:
        if code is not None:
            idx.add(vendor, bizno, int(code))
    return idx


def build_seed_from_inputrows(rows, *, config_dir: str | None = None) -> SeedIndex:
    """다운로드 배치의 '이미 분류된' 행(차변계정 보유)에서 자가 시딩.

    차변계정명을 코드로 매핑해 (거래처/사업자번호)별 빈도로 적재한다.
    미추천 행(차변계정 비어있음)은 시드에서 제외.
    """
    idx = SeedIndex()
    for row in rows:
        name = (row.차변계정 or "").strip()
        if not name:
            continue
        res = map_account_name_to_code(name, config_dir=config_dir)
        if res.value is not None:
            idx.add(row.거래처, row.사업자등록번호, int(res.value))
    return idx
