"""유형 기준표(config type_policy)가 담당자 판단과 얼마나 맞는지 — 담당자가 정한 행(전표확정·답지)과 비교.

기준표를 켜기 전에 잰다(켜져 있지 않아도 잰다). 출력은 건수와 계정 이름뿐(수임처·거래처 이름 없음).

    python -m kafa.learn.type_check [--db ~/kafa-out/kafa.db]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TypeCheck:
    rows: int = 0
    clients: int = 0
    decided: int = 0
    agree: int = 0
    misses: Counter = field(default_factory=Counter)      # (계정, 기준표, 담당자) → 건수
    undecided: Counter = field(default_factory=Counter)   # 계정 → 건수

    @property
    def rate(self) -> float:
        return self.agree / self.decided if self.decided else 0.0


def check_type_policy(records, *, config_dir: str | None = None) -> TypeCheck:
    """records: [(client_id, 차변계정코드, 세액, 담당자 유형 이름)] — VoucherStore.father_type_rows."""
    from kafa.config_loader import client_profile, load_rules
    from kafa.recommend.host_picks import account_names
    from kafa.rules.type_policy import decide_type, has_vat, policy

    pol = policy(config_dir)
    labels = {int(v): str(k) for k, v in (load_rules(config_dir).get("vat_type_codes") or {}).items()}
    names = account_names(config_dir)
    out = TypeCheck()
    seen = set()
    profiles: dict[str, dict] = {}
    for client, code, tax, father in records:
        out.rows += 1
        seen.add(client)
        prof = profiles.setdefault(client, client_profile(client, config_dir))
        d = decide_type(code, has_vat(tax), prof, pol, config_dir)
        name = names.get(code, str(code))
        if d is None:
            out.undecided[name] += 1
            continue
        out.decided += 1
        if isinstance(father, int):                 # 예전 모양(유형코드)도 받는다
            father = labels.get(father, str(father))
        if d.label == father:
            out.agree += 1
        else:
            out.misses[(name, d.label, father)] += 1
    out.clients = len(seen)
    return out


def render(r: TypeCheck) -> str:
    lines = [f"담당자가 정한 행 {r.rows}건(수임처 {r.clients}곳)",
             f"기준표가 정한 행 {r.decided}건 중 담당자와 같은 유형 {r.agree}건 ({r.rate:.0%})",
             f"기준표에 없는 계정(위하고 값 그대로) {sum(r.undecided.values())}건"]
    if r.misses:
        lines.append("다른 곳(계정 · 기준표 → 담당자):")
        lines += [f"  {n} · {a} → {b}: {k}건" for (n, a, b), k in r.misses.most_common(10)]
    if r.undecided:
        lines.append("기준표에 없는 계정: " + " / ".join(f"{n} {k}" for n, k in r.undecided.most_common(6)))
    if not r.rows:
        lines.append("담당자가 정한 행이 아직 없습니다 — python -m kafa.store.backfill 로 원본의 "
                     "'전표확정'을 먼저 되살리거나, kafa-desk 답지가 쌓인 뒤에 다시 재세요.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from kafa.store.db import VoucherStore
    ap = argparse.ArgumentParser(prog="python -m kafa.learn.type_check",
                                 description="유형 기준표를 담당자가 정한 행과 맞춰 본다(건수만)")
    ap.add_argument("--db", default="~/kafa-out/kafa.db")
    args = ap.parse_args(argv)
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"[중단] DB 가 없습니다: {db_path}", file=sys.stderr)
        return 2
    with VoucherStore(db_path) as db:
        records = db.father_type_rows()
    print(render(check_type_policy(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
