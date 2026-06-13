"""정확도 검증 하니스 — 도구 분류 결과를 담당자 '수작업 정답'과 대조.

spec 수용 기준:
  "익명화 샘플을 돌려 아버님 수작업 결과와 대조, 자동 처리 정확도 목표(≥95%),
   불일치 사유별 리포트."

정답(truth)은 담당자가 라벨링한 CSV(로컬). 매칭 키 = (사업자번호, 합계, 품명).
필드별 정확도(차변계정코드·유형코드·공제여부)와 자동처리율, 불일치 목록을 산출한다.
원천 데이터는 로컬 처리. 외부 노출 요약은 마스킹.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from kafa.rules.models import ClassifiedRow, Deduct, Verdict
from kafa.security import mask_name

_DEDUCT_LABEL = {
    Deduct.DEDUCTIBLE: "공제",
    Deduct.NON_DEDUCTIBLE: "불공제",
    Deduct.REVIEW: "검토",
}

# 비교 대상 필드: (이름, ClassifiedRow에서 라벨 추출 함수)
_FIELDS = ("차변계정코드", "유형코드", "공제여부")


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def match_key(사업자번호: str, 합계, 품명: str) -> str:
    return f"{_digits(사업자번호)}|{합계}|{(품명 or '').strip()}"


@dataclass
class TruthRecord:
    차변계정코드: Optional[int] = None
    유형코드: Optional[int] = None
    공제여부: Optional[str] = None     # "공제"/"불공제"/"검토"


@dataclass
class EvalResult:
    total: int = 0
    matched: int = 0
    unmatched: int = 0                 # 정답에 매칭 안 된 분류 행
    auto_done: int = 0
    field_comparable: Counter = field(default_factory=Counter)  # 필드별 비교 가능 건수
    field_correct: Counter = field(default_factory=Counter)     # 필드별 일치 건수
    mismatch_by_field: Counter = field(default_factory=Counter)
    mismatches: list[str] = field(default_factory=list)         # (마스킹) 불일치 상세

    @property
    def automation_rate(self) -> float:
        return (self.auto_done / self.total) if self.total else 0.0

    def field_accuracy(self, field_name: str) -> Optional[float]:
        n = self.field_comparable[field_name]
        return (self.field_correct[field_name] / n) if n else None

    @property
    def overall_accuracy(self) -> Optional[float]:
        comp = sum(self.field_comparable.values())
        corr = sum(self.field_correct.values())
        return (corr / comp) if comp else None


def load_truth_csv(path: str | Path) -> dict[str, TruthRecord]:
    """담당자 정답 CSV → {match_key: TruthRecord}.

    필수 컬럼: 사업자번호, 합계, 품명. 정답 컬럼(선택): 차변계정코드, 유형코드, 공제여부.
    """
    out: dict[str, TruthRecord] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            key = match_key(row.get("사업자번호", ""), row.get("합계", ""),
                            row.get("품명", ""))

            def _int(v):
                v = (v or "").strip()
                return int(float(v)) if v else None

            out[key] = TruthRecord(
                차변계정코드=_int(row.get("차변계정코드")),
                유형코드=_int(row.get("유형코드")),
                공제여부=(row.get("공제여부") or "").strip() or None,
            )
    return out


def _predicted(r: ClassifiedRow) -> dict[str, object]:
    return {
        "차변계정코드": r.차변계정코드,
        "유형코드": r.유형코드,
        "공제여부": _DEDUCT_LABEL.get(r.공제여부, None),
    }


def evaluate(rows: list[ClassifiedRow], truth: dict[str, TruthRecord],
             *, mask: bool = True) -> EvalResult:
    res = EvalResult()
    for r in rows:
        if r.skipped or r.source is None:
            continue
        res.total += 1
        if r.판정유형 in (Verdict.RULE_CONFIRMED, Verdict.RECOMMENDED) and not r.needs_review:
            res.auto_done += 1

        src = r.source
        key = match_key(src.사업자등록번호, src.합계, src.품명)
        t = truth.get(key)
        if t is None:
            res.unmatched += 1
            continue
        res.matched += 1

        pred = _predicted(r)
        for fname in _FIELDS:
            expected = getattr(t, fname)
            if expected is None:
                continue                # 정답 미라벨 → 비교 제외
            res.field_comparable[fname] += 1
            got = pred[fname]
            if got == expected:
                res.field_correct[fname] += 1
            else:
                res.mismatch_by_field[fname] += 1
                tag = mask_name(src.거래처) if mask else src.거래처
                res.mismatches.append(
                    f"[{tag}] {fname}: 정답={expected} / 예측={got} "
                    f"({r.판정유형.value}, 근거 {','.join(r.판정근거)})")
    return res


def render_eval(res: EvalResult, *, target: float = 0.95) -> str:
    lines = [
        "── 정확도 검증 (담당자 수작업 대조) ──",
        f"대상 {res.total} | 매칭 {res.matched} | 미매칭 {res.unmatched}",
        f"자동처리율 {res.automation_rate:.1%}",
    ]
    acc = res.overall_accuracy
    if acc is not None:
        flag = "✅" if acc >= target else "⚠️"
        lines.append(f"전체 정확도 {acc:.1%} {flag} (목표 {target:.0%})")
    for fname in _FIELDS:
        fa = res.field_accuracy(fname)
        if fa is not None:
            lines.append(f"  - {fname}: {fa:.1%} "
                         f"({res.field_correct[fname]}/{res.field_comparable[fname]})")
    if res.mismatches:
        lines.append(f"\n● 불일치 {len(res.mismatches)}건 (사유별: "
                     + ", ".join(f"{k} {v}" for k, v in res.mismatch_by_field.items()) + ")")
        for m in res.mismatches[:50]:
            lines.append(f"  - {m}")
    return "\n".join(lines)
