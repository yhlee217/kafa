"""미해소(계정이 빈) 건을 화면에 보여준다 — 담당자 눈으로 확인하는 용도.

**거래처 실명은 이 화면에만 남는다.** 보안 제0원칙에 따라 실명은 어떤 LLM 컨텍스트에도
올리지 않으므로, 아래쪽에 **실명 없는 요약**(업태·종목·품명·건수)을 따로 찍는다.
계정을 함께 정하고 싶으면 그 요약만 옮기면 된다 — 추천 엔진도 같은 특징만 쓴다
(kafa/recommend/features.py).

    python tools/show_unresolved.py ~/kafa-보충/미해소_수기입력.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def _amount(text) -> int:
    try:
        return int(float(str(text or 0).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="show_unresolved", description=__doc__)
    ap.add_argument("csv_path", nargs="?",
                    default="~/kafa-보충/미해소_수기입력.csv")
    ap.add_argument("--by", choices=["금액", "수임처"], default="금액",
                    help="정렬 기준(기본: 금액 큰 순)")
    args = ap.parse_args(argv)

    path = Path(args.csv_path).expanduser()
    if not path.exists():
        print(f"파일이 없습니다: {path}")
        print("먼저 --skip-unresolved 로 보충본을 만드세요.")
        return 2

    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("미해소 건이 없습니다.")
        return 0

    rows.sort(key=(lambda r: -_amount(r.get("합계")))
              if args.by == "금액" else
              (lambda r: (r.get("수임처", ""), r.get("거래일자", ""))))

    총액 = sum(_amount(r.get("합계")) for r in rows)
    print(f"미해소 {len(rows)}건 / {총액:,}원   ({path})\n")
    print(f"{'수임처':<14} {'날짜':<11} {'거래처':<28} {'금액':>10}  업태/종목")
    print("─" * 96)
    for r in rows:
        print(f"{(r.get('수임처') or '')[:14]:<14} {(r.get('거래일자') or ''):<11} "
              f"{(r.get('거래처') or '')[:28]:<28} {_amount(r.get('합계')):>10,}  "
              f"{(r.get('업태') or '')[:10]}/{(r.get('종목') or '')[:16]}")

    print("\n── 실명 없는 요약 (계정을 같이 정하려면 이것만 옮기세요) ──")
    묶음 = Counter((r.get("업태") or "", r.get("종목") or "", r.get("품명") or "")
                  for r in rows)
    금액별: dict[tuple, int] = {}
    for r in rows:
        k = (r.get("업태") or "", r.get("종목") or "", r.get("품명") or "")
        금액별[k] = 금액별.get(k, 0) + _amount(r.get("합계"))
    for k, n in 묶음.most_common():
        u, j, p = k
        print(f"  {n}건 {금액별[k]:>9,}원 | 업태={u or '(공란)'} | 종목={j or '(공란)'} "
              f"| 품명={p or '(공란)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
