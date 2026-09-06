"""조회 의뢰서 — 어느 수임처의 어느 기간을 받아와야 하는지.

1,803건을 **건별로** 조회하면 안 된다(건당 20초만 잡아도 10시간이다).
카드사 이용내역은 기간 단위로 한 번에 받아지므로, 의뢰서도 기간 단위로 만든다.

파일이 셋으로 갈리는 이유는 보안이다:
- `lookup_plan.csv`   화면을 도는 조수(AI)가 보는 것 — **실명 없음**, 번호로만 부른다
- `lookup_index.csv`  번호 ↔ 수임처 실명 대응표 — **사람 전용**
- `lookup_targets.csv` 로컬 대조용 전체 목록 — 코드만 읽는다
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from kafa.rules.agents import agent_of


@dataclass
class LookupPlan:
    """수임처 한 곳에 대한 조회 의뢰 한 줄."""
    번호: int
    client_id: str
    수임처: str
    기간_시작: str
    기간_끝: str
    건수: int
    금액합: Decimal = Decimal(0)
    갈래: dict[str, int] = field(default_factory=dict)

    @property
    def 갈래표기(self) -> str:
        return " ".join(f"{g}:{n}" for g, n in sorted(self.갈래.items()))


def _amount(text: str) -> Decimal:
    try:
        return Decimal(str(text or "0").replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal(0)


def _agent_rows(db_path: str | Path, *, groups: set[str] | None = None,
                config_dir: str | None = None) -> list[tuple]:
    """대행사로 판정된 행만 뽑는다. 스킵된 행은 애초에 전표가 아니다."""
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "select client_id, 거래일자, 합계, 거래처, 업태, 종목 from vouchers "
            "where coalesce(skipped, 0) = 0"
        ).fetchall()
        names = {r["client_id"]: r["name"] for r in
                 con.execute("select client_id, name from clients")}
    finally:
        con.close()

    out = []
    for r in rows:
        found = agent_of(r["거래처"] or "", 업태=r["업태"] or "",
                         종목=r["종목"] or "", config_dir=config_dir)
        if not found:
            continue
        group = found[0]
        if groups and group not in groups:
            continue
        out.append((r["client_id"], names.get(r["client_id"], r["client_id"]),
                    r["거래일자"] or "", _amount(r["합계"]), r["거래처"] or "", group))
    return out


def build_plan(db_path: str | Path, out_dir: str | Path, *,
               groups: set[str] | None = None,
               config_dir: str | None = None) -> list[LookupPlan]:
    """의뢰서 3종을 만들고 계획을 돌려준다."""
    rows = _agent_rows(db_path, groups=groups, config_dir=config_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    plans: dict[str, LookupPlan] = {}
    for client_id, 수임처, 날짜, 금액, _거래처, 갈래 in rows:
        p = plans.get(client_id)
        if p is None:
            p = plans[client_id] = LookupPlan(
                번호=0, client_id=client_id, 수임처=수임처,
                기간_시작=날짜, 기간_끝=날짜, 건수=0)
        p.건수 += 1
        p.금액합 += 금액
        p.갈래[갈래] = p.갈래.get(갈래, 0) + 1
        if 날짜:
            p.기간_시작 = min(p.기간_시작 or 날짜, 날짜)
            p.기간_끝 = max(p.기간_끝 or 날짜, 날짜)

    ordered = sorted(plans.values(), key=lambda p: (-p.건수, p.수임처))
    for i, p in enumerate(ordered, start=1):
        p.번호 = i

    # 조수가 보는 것 — 실명 없음
    with (out / "lookup_plan.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "기간_시작", "기간_끝", "건수", "갈래"])
        for p in ordered:
            w.writerow([p.번호, p.기간_시작, p.기간_끝, p.건수, p.갈래표기])

    # 사람 전용 대응표
    with (out / "lookup_index.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "수임처", "client_id", "건수", "금액합"])
        for p in ordered:
            w.writerow([p.번호, p.수임처, p.client_id, p.건수, p.금액합])

    # 로컬 대조용 전체 목록
    by_id = {p.client_id: p.번호 for p in ordered}
    with (out / "lookup_targets.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "client_id", "수임처", "거래일자", "합계", "거래처", "갈래"])
        for client_id, 수임처, 날짜, 금액, 거래처, 갈래 in rows:
            w.writerow([by_id.get(client_id, 0), client_id, 수임처, 날짜,
                        금액, 거래처, 갈래])
    return ordered
