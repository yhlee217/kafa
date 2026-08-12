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


def industry_keys(업태: str, 종목: str) -> list[str]:
    """업종 시드 키(구체적인 것부터). '업태|종목' → '업태'."""
    u = _NON_ALNUM.sub("", (업태 or "")).lower()
    j = _NON_ALNUM.sub("", (종목 or "")).lower()
    keys = []
    if u and j:
        keys.append(f"{u}|{j}")
    if u:
        keys.append(u)
    return keys


@dataclass
class SeedIndex:
    """거래처(정규화)/사업자번호/업종 → 계정코드 빈도.

    by_industry 는 **이 수임처가 그 업종을 어떤 계정으로 처리해왔는지**를 담는다.
    처음 보는 가맹점(가맹점 시드로는 못 푸는 건)의 마지막 단서이며, 기준이
    수임처마다 다른 항목(예: 음식점 = 접대비냐 복리후생비냐)을 고객별로 흡수한다.
    """
    by_vendor: dict[str, Counter] = field(default_factory=dict)
    by_bizno: dict[str, Counter] = field(default_factory=dict)
    by_industry: dict[str, Counter] = field(default_factory=dict)

    def add(self, vendor: str, bizno: str, account_code: int,
            업태: str = "", 종목: str = "") -> None:
        vk = vendor_key(vendor)
        if vk:
            self.by_vendor.setdefault(vk, Counter())[account_code] += 1
        bk = bizno_key(bizno)
        if bk:
            self.by_bizno.setdefault(bk, Counter())[account_code] += 1
        for ik in industry_keys(업태, 종목):
            self.by_industry.setdefault(ik, Counter())[account_code] += 1

    def top_by_industry(self, 업태: str, 종목: str, *, min_support: int = 3,
                        min_ratio: float = 0.65):
        """업종 최빈 계정 → (코드, 점유율, 키, 건수). 근거 부족하면 None.

        건수(min_support)와 편중(min_ratio)을 모두 넘겨야 한다. 반반으로 갈리는 업종은
        추천하지 않고 담당자 확인으로 넘기는 게 맞다(틀린 자동 확정보다 안전).
        구체적인 키(업태|종목)를 먼저 보고, 근거가 모자라면 상위 키(업태)로 내려간다.
        """
        for ik in industry_keys(업태, 종목):
            c = self.by_industry.get(ik)
            if not c:
                continue
            total = sum(c.values())
            if total < min_support:
                continue
            code, n = c.most_common(1)[0]
            share = n / total
            if share < min_ratio:
                continue
            return code, share, ik, total
        return None

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
        return not self.by_vendor and not self.by_bizno and not self.by_industry


def build_seed_index(records: Iterable[tuple]) -> SeedIndex:
    """과거 처리분 레코드 → SeedIndex.

    레코드는 (거래처, 사업자번호, 계정코드) 또는 업종까지 포함한
    (거래처, 사업자번호, 계정코드, 업태, 종목) 둘 다 받는다.
    TODO: 최근성 가중 추가.
    """
    idx = SeedIndex()
    for rec in records or []:
        vendor, bizno, code = rec[0], rec[1], rec[2]
        업태 = rec[3] if len(rec) > 3 else ""
        종목 = rec[4] if len(rec) > 4 else ""
        if code is not None:
            idx.add(vendor, bizno, int(code), 업태 or "", 종목 or "")
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
            idx.add(row.거래처, row.사업자등록번호, int(res.value),
                    row.업태, row.종목)
    return idx
