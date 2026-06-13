"""Phase 2 시드 — 위하고 과거 처리분 전표에서 (거래처/적요 → 계정) 빈도 인덱스 구축.

[골격] 과거 처리분 엑셀 미확보. 시드 확보 시 빈도·최근성 기반 인덱스를 만든다.
원천 데이터는 로컬 코드에서만 처리(보안 제0원칙).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class SeedIndex:
    """거래처(정규화) → 계정코드 빈도. 적요 보강 가능."""
    by_vendor: dict[str, Counter] = field(default_factory=dict)

    def add(self, vendor_key: str, account_code: int) -> None:
        self.by_vendor.setdefault(vendor_key, Counter())[account_code] += 1

    def top(self, vendor_key: str) -> tuple[int, float] | None:
        """최빈 계정코드와 점유율(0~1)을 반환. 없으면 None."""
        c = self.by_vendor.get(vendor_key)
        if not c:
            return None
        code, n = c.most_common(1)[0]
        return code, n / sum(c.values())


def build_seed_index(records) -> SeedIndex:
    """과거 처리분 레코드[(vendor_key, account_code), ...] → SeedIndex.

    TODO: 위하고 과거 전표 엑셀 리더와 연결. 최근성 가중 추가.
    """
    idx = SeedIndex()
    for vendor_key, code in records or []:
        if vendor_key and code is not None:
            idx.add(vendor_key, int(code))
    return idx
