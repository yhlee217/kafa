"""kafa-lookup — 대행사 건의 원본 결제내역 되찾기.

    kafa-lookup plan  <kafa.db> <낼 폴더> [--groups 결제대행,오픈마켓·배달]
    kafa-lookup merge  <lookup_targets.csv> <이용내역 폴더|파일…> [-o 결과.csv]
    kafa-lookup stage2 <resolved.csv> [-o 조회목록.csv] [--min-amount N] [--top N]

`plan` 은 무엇을 받아와야 하는지 의뢰서를 만들고, `merge` 는 받아온 이용내역을
로컬에서 붙인다. 사이의 '카드사에서 받아오기'는 사람이 로그인하고 조수가 화면을
도는 단계다(docs/cowork_lookup.md).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kafa.lookup.merge import merge_statements
from kafa.lookup.request import build_plan
from kafa.lookup.stage2 import build_tasks, summarize

_SUFFIXES = {".csv", ".xlsx", ".xls", ".xlsm"}


def _expand(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in _SUFFIXES))
        elif p.exists():
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kafa-lookup", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="조회 의뢰서 만들기")
    plan.add_argument("db")
    plan.add_argument("out_dir")
    plan.add_argument("--groups", help="갈래 제한(쉼표로 구분). 기본은 전부")
    plan.add_argument("--config-dir")

    merge = sub.add_parser("merge", help="받아온 이용내역 붙이기")
    merge.add_argument("targets")
    merge.add_argument("statements", nargs="+", help="이용내역 파일 또는 폴더")
    merge.add_argument("-o", "--out", default="lookup_resolved.csv")
    merge.add_argument("--config-dir")

    two = sub.add_parser("stage2", help="1단계로 안 풀린 건을 어디서 찾을지 목록화")
    two.add_argument("resolved")
    two.add_argument("-o", "--out", default="lookup_stage2.csv")
    two.add_argument("--min-amount", type=int, help="이 금액 미만은 뺀다")
    two.add_argument("--top", type=int, help="금액 상위 몇 건만")
    two.add_argument("--config-dir")

    args = ap.parse_args(argv)

    if args.cmd == "plan":
        groups = ({g.strip() for g in args.groups.split(",") if g.strip()}
                  if args.groups else None)
        plans = build_plan(args.db, args.out_dir, groups=groups,
                           config_dir=args.config_dir)
        total = sum(p.건수 for p in plans)
        print(f"조회 의뢰서: 수임처 {len(plans)}곳 / 대행사 {total}건")
        print(f"  {args.out_dir}/lookup_plan.csv    ← 조수에게 주는 것(실명 없음)")
        print(f"  {args.out_dir}/lookup_index.csv   ← 번호↔수임처 대응표(사람 전용)")
        print(f"  {args.out_dir}/lookup_targets.csv ← 로컬 대조용")
        for p in plans[:10]:
            print(f"   {p.번호:>3}  {p.기간_시작}~{p.기간_끝}  {p.건수:>4}건  {p.갈래표기}")
        if len(plans) > 10:
            print(f"   … 외 {len(plans) - 10}곳")
        return 0

    if args.cmd == "stage2":
        tasks = build_tasks(args.resolved, args.out, min_amount=args.min_amount,
                            top_n=args.top, config_dir=args.config_dir)
        total = sum(abs(t.금액) for t in tasks)
        print(f"2단계 조회 목록: {len(tasks)}건 / {total:,}원")
        print(summarize(tasks))
        print(f"  → {args.out}  (찾은가맹점·품명 칸을 채워 넣으면 된다)")
        return 0

    files = _expand(args.statements)
    if not files:
        print("이용내역 파일이 없습니다.", file=sys.stderr)
        return 2
    _, report = merge_statements(args.targets, files, args.out,
                                 config_dir=args.config_dir)
    print(f"이용내역 {len(files)}개 파일")
    print(report.요약())
    if report.갈래별_찾음:
        print("  갈래별 찾음:", " ".join(f"{k}:{v}" for k, v in
                                    sorted(report.갈래별_찾음.items())))
    print(f"  → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
