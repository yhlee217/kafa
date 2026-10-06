"""업태 표기 통일이 실제 자료에서 얼마나 효과가 있는지 재본다 (읽기 전용).

업종 추천은 **건수 3건·편중 65%** 를 둘 다 넘겨야 한다. 표기가 쪼개져 있으면 문턱을
못 넘어 미해소로 남는다. 묶은 뒤 문턱을 넘는 바구니가 몇 개 늘고, 그 바구니에 걸린
미해소가 몇 건인지 센다.

    python tools/industry_report.py ~/kafa-out/kafa.db
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from kafa.recommend.seed import canonical_industry

MIN_SUPPORT = 3
MIN_RATIO = 0.65


def _threshold_pass(counter: Counter) -> bool:
    total = sum(counter.values())
    if total < MIN_SUPPORT:
        return False
    return counter.most_common(1)[0][1] / total >= MIN_RATIO


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="industry_report", description=__doc__)
    ap.add_argument("db", nargs="?", default="~/kafa-out/kafa.db")
    args = ap.parse_args(argv)

    path = Path(args.db).expanduser()
    if not path.exists():
        print(f"DB 가 없습니다: {path}")
        return 2
    con = sqlite3.connect(path)
    rows = list(con.execute(
        "select client_id, 업태, 차변계정코드 from vouchers "
        "where coalesce(skipped, 0) = 0"))
    con.close()
    if not rows:
        print("전표가 없습니다.")
        return 0

    # 수임처별로 따로 센다 — 업종 이력은 그 수임처 기준이다.
    원본: dict[tuple, Counter] = defaultdict(Counter)
    바구니: dict[tuple, Counter] = defaultdict(Counter)
    미해소_원본: Counter = Counter()
    미해소_바구니: Counter = Counter()
    for client, 업태, code in rows:
        raw = (업태 or "").strip()
        bucket = canonical_industry(raw)
        if code is None:
            미해소_원본[(client, raw.lower())] += 1
            미해소_바구니[(client, bucket)] += 1
        else:
            원본[(client, raw.lower())][code] += 1
            바구니[(client, bucket)][code] += 1

    표기수 = len({(업태 or "").strip().lower() for _, 업태, _ in rows})
    바구니수 = len({canonical_industry((업태 or "").strip()) for _, 업태, _ in rows})
    print(f"전표 {len(rows):,}건 / 수임처 {len({r[0] for r in rows})}곳")
    print(f"업태 표기 {표기수}종 → 바구니 {바구니수}종 "
          f"({표기수 - 바구니수}종이 합쳐짐)\n")

    전 = sum(n for k, n in 미해소_원본.items() if _threshold_pass(원본.get(k, Counter())))
    후 = sum(n for k, n in 미해소_바구니.items() if _threshold_pass(바구니.get(k, Counter())))
    총미해소 = sum(미해소_원본.values())
    print(f"미해소 {총미해소:,}건 중 업종 이력으로 해소 가능한 건수")
    print(f"  통일 전: {전:,}건 ({전 / max(총미해소, 1) * 100:.1f}%)")
    print(f"  통일 후: {후:,}건 ({후 / max(총미해소, 1) * 100:.1f}%)")
    print(f"  늘어난 몫: {후 - 전:,}건")

    늘어난: Counter = Counter()
    for (client, bucket), n in 미해소_바구니.items():
        if _threshold_pass(바구니.get((client, bucket), Counter())):
            늘어난[bucket] += n
    if 늘어난:
        print("\n해소 가능한 미해소가 많은 바구니:")
        for bucket, n in 늘어난.most_common(8):
            print(f"  {bucket or '(공란)':20} {n:>5}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
