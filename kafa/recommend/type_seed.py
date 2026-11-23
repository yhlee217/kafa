"""유형(카과/일반) 추천 — 아직 처리 전인 전표의 유형을 같은 가맹점 이력으로 채운다.

담당자 확인(2026-11-23): 공제 여부는 담당자가 위하고에서 정하는 **유형**이 가른다
(카과 → 매입매출·세액공제, 일반 → 일반전표). 그런데 아직 처리 전인 전표는 유형이 국세청 값
그대로라(실측: 어긋남 1.7%), 공제 기준을 유형으로 바꿔도 이 행들은 달라지지 않는다.
그래서 이 수임처가 **같은 가맹점**을 지난번에 어떤 유형으로 처리했는지를 본다.

- 근거는 같은 수임처의 같은 가맹점(사업자번호 → 거래처명)만 쓴다. 업종 단위로 넓히면
  같은 업종이라도 수임처·용도에 따라 공제가 갈려 틀린 공제가 생긴다.
- 이력은 **처리된 행**(차변계정이 채워진 행)에서만 배운다 — 도구가 추천한 행을 다시 배우면
  추천이 추천을 키운다.
- 기준값은 config `type_recommend`. 근거는 docs/domain_notes.md.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from kafa.config_loader import load_rules
from kafa.recommend.seed import bizno_key, vendor_key

RULE_TYPE_HIST = "TYPE-HIST"     # 같은 가맹점 이력으로 유형을 채움


@dataclass(frozen=True)
class TypeSuggestion:
    label: str          # 추천 유형 라벨(카과/일반 …)
    share: float        # 이력 중 그 유형의 비율
    support: int        # 이력 건수
    basis: str          # '사업자번호' | '거래처명'


@dataclass
class TypeIndex:
    """수임처 한 곳의 (가맹점 → 유형 빈도)."""
    by_bizno: dict[str, Counter] = field(default_factory=dict)
    by_vendor: dict[str, Counter] = field(default_factory=dict)

    def add(self, vendor: str, bizno: str, label: str) -> None:
        label = (label or "").strip()
        if not label:
            return
        bk = bizno_key(bizno)
        if bk:
            self.by_bizno.setdefault(bk, Counter())[label] += 1
        vk = vendor_key(vendor)
        if vk:
            self.by_vendor.setdefault(vk, Counter())[label] += 1

    def merge(self, other: "TypeIndex") -> "TypeIndex":
        for mine, theirs in ((self.by_bizno, other.by_bizno),
                             (self.by_vendor, other.by_vendor)):
            for k, c in theirs.items():
                mine.setdefault(k, Counter()).update(c)
        return self

    @property
    def empty(self) -> bool:
        return not self.by_bizno and not self.by_vendor

    def suggest(self, vendor: str, bizno: str, *, min_support: int = 2,
                min_share: float = 0.9) -> TypeSuggestion | None:
        """사업자번호 이력부터, 없으면 거래처명 이력. 건수·편중을 둘 다 넘겨야 한다."""
        for basis, counter in (("사업자번호", self.by_bizno.get(bizno_key(bizno))),
                               ("거래처명", self.by_vendor.get(vendor_key(vendor)))):
            if not counter:
                continue
            total = sum(counter.values())
            label, n = counter.most_common(1)[0]
            if total >= min_support and n / total >= min_share:
                return TypeSuggestion(label, round(n / total, 3), total, basis)
            return None          # 이력은 있는데 갈린다 → 더 넓은 근거로 내려가지 않는다
        return None


def _processed(row) -> bool:
    """처리된 행 — 차변계정이 채워졌고 중복전표가 아니다."""
    return bool((row.차변계정 or "").strip()) and (row.전표상태 or "").strip() != "중복전표"


def build_type_index_from_inputrows(rows: Iterable) -> TypeIndex:
    """이번 파일의 처리된 행에서 배운다(자가 시딩)."""
    idx = TypeIndex()
    for r in rows:
        if _processed(r):
            idx.add(r.거래처, r.사업자등록번호, r.유형)
    return idx


def build_type_index(records: Iterable[tuple]) -> TypeIndex:
    """DB 이력 (거래처, 사업자번호, 유형 라벨) → TypeIndex."""
    idx = TypeIndex()
    for vendor, bizno, label in records or []:
        idx.add(vendor or "", bizno or "", label or "")
    return idx


def type_settings(config_dir: str | None = None) -> dict:
    cfg = load_rules(config_dir).get("type_recommend") or {}
    return {"enabled": bool(cfg.get("enabled", False)),
            "min_support": int(cfg.get("min_support", 2)),
            "min_share": float(cfg.get("min_share", 0.9))}
