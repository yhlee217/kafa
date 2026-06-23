"""초안5 — 전월 대비 거래처/계정 변동 대사(해시 기반 · PII 안전).

매월 신규 거래처 출현·계정 분류 일관성을 수기로 확인하던 작업을 자동화한다.
거래처는 **해시**로만 기준선(baseline)에 보존하므로 로컬 파일에도 실명을 남기지 않는다
(dup_guard 와 동일 원칙). 출력의 거래처는 마스킹.

- 신규 거래처: 기준선에 없는 거래처 해시.
- 계정 변동: 같은 거래처(해시)인데 직전과 다른 차변계정코드 → 분류 일관성 점검.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kafa.rules.models import ClassifiedRow
from kafa.security import hash_id, mask_name


# 계정 미정(미추천) 거래처를 '봤지만 코드 없음'으로 표시(유효 계정코드는 양수).
# 미정 거래처가 매달 '신규'로 재플래그되는 것을 막는다.
_SEEN_NO_CODE = 0


def _vendor_key(name: str) -> str:
    return hash_id(name, salt="recon", length=16)


@dataclass
class ReconResult:
    new_vendors: list[str] = field(default_factory=list)            # 마스킹 거래처명
    account_changes: list[tuple] = field(default_factory=list)      # (마스킹명, 이전코드, 현재코드)

    @property
    def has_findings(self) -> bool:
        return bool(self.new_vendors or self.account_changes)


def reconcile(rows: list[ClassifiedRow],
              baseline: dict[str, int]) -> tuple[ReconResult, dict[str, int]]:
    """직전 기준선(거래처해시→차변계정코드) 대비 신규/변동을 찾고, 갱신된 기준선을 반환."""
    res = ReconResult()
    new_baseline = dict(baseline)
    seen_new: set[str] = set()
    for r in rows:
        if r.skipped or not r.source or not r.source.거래처:
            continue
        name = r.source.거래처
        key = _vendor_key(name)
        code = r.차변계정코드
        if key not in baseline:
            if key not in seen_new:          # 신규는 한 번만 보고
                res.new_vendors.append(mask_name(name))
                seen_new.add(key)
        else:
            prev = new_baseline.get(key)     # 배치 내 변경도 반영(live 기준선)
            if (code is not None and prev not in (None, _SEEN_NO_CODE)
                    and prev != code):
                res.account_changes.append((mask_name(name), prev, code))
        # 기준선 갱신: 코드 있으면 코드, 없으면 '봤음' 표시 유지(미정도 재신규 방지)
        new_baseline[key] = code if code is not None else \
            new_baseline.get(key, _SEEN_NO_CODE)
    return res, new_baseline


def render_recon(res: ReconResult) -> str:
    lines = ["── 거래처/계정 변동 대사(전월 대비) ──"]
    if not res.has_findings:
        lines.append("  신규 거래처·계정 변동 없음 ✅")
        return "\n".join(lines)
    if res.new_vendors:
        lines.append(f"  신규 거래처 {len(res.new_vendors)}건:")
        for v in res.new_vendors:
            lines.append(f"    + {v}")
    if res.account_changes:
        lines.append(f"  계정 변동 {len(res.account_changes)}건(분류 일관성 점검):")
        for name, old, new in res.account_changes:
            lines.append(f"    ~ {name}: {old} → {new}")
    return "\n".join(lines)


class VendorBaseline:
    """거래처 기준선(해시→코드)을 로컬 JSON 으로 보존. 평문 미보존(PII 안전)."""

    def __init__(self, store_path: str | Path):
        self.store_path = Path(store_path)
        self.mapping: dict[str, int] = {}
        if self.store_path.exists():
            try:
                self.mapping = {k: int(v) for k, v in
                                json.loads(self.store_path.read_text("utf-8")).items()}
            except (json.JSONDecodeError, OSError, ValueError):
                self.mapping = {}

    def save(self, mapping: dict[str, int]) -> None:
        self.mapping = dict(mapping)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(self.mapping, ensure_ascii=False), "utf-8")
