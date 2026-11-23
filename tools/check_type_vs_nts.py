"""'유형'(카과/일반) 과 '국세청'(공제/불공제) 이 얼마나 어긋나는지 센다 (읽기 전용).

담당자 통화(2026-11-23): 공제 여부는 국세청 칸이 아니라 담당자가 위하고에서 정하는 **유형**
으로 가른다 — 카과는 매입매출, 일반은 일반전표. 그런데 지금 kafa 는 국세청 칸으로 판정한다.
고치기 전에 확인할 것: 내려받은 파일의 유형 칸이 담당자가 고른 결과인가, 위하고 기본값인가.

  - 유형이 국세청과 늘 같으면 → 유형은 국세청을 그대로 옮긴 기본값일 가능성이 크다
  - 어긋나는 행이 있고, 그게 처리된 전표(차변계정이 채워진 행)에 몰려 있으면
    → 담당자가 유형을 고친 흔적이다. 유형 칸을 기준으로 쓸 수 있다

계정과 유형 (2026-11-23 추가): 처리 전 행은 같은 가맹점 이력이 거의 없다(위하고가 이미
가맹점 단위로 계정을 채우므로, 남은 건 처음 보는 가맹점). 대신 **계정이 유형을 정해 주는지**
본다 — 예: 접대비는 부가세법상 매입세액 불공제라 유형은 '일반' 이어야 한다.
  - 처리된 행의 계정별 유형 분포
  - 처리 전 행을 지금 도구로 분류했을 때 계정별 공제 여부, 특히 '접대비인데 공제' 건수

DB 도 설정도 건드리지 않고 원본 엑셀만 읽는다. 출력은 건수·금액만(수임처 이름 없음).

    python tools/check_type_vs_nts.py ~/kafa-out/_archive
"""
from __future__ import annotations

import argparse
import sys
import warnings
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

# 위하고 다운로드본은 기본 스타일이 없어 openpyxl 이 파일마다 경고를 낸다 — 출력만 가린다.
warnings.filterwarnings("ignore", message="Workbook contains no default style",
                        module="openpyxl")

from kafa.io_wehago.reader import read_download_xlsx  # noqa: E402
from kafa.recommend.type_seed import (build_type_index_from_inputrows,  # noqa: E402
                                      type_settings)

ENTERTAINMENT = "접대비"      # 이름에 이 말이 든 계정(513·613·713·813·843 …)

# 유형이 국세청과 '같은 뜻' 인 짝. 그 밖의 조합은 어긋난 것으로 본다.
AGREE = {("카과", "공제"), ("카면", "공제"), ("카영", "공제"), ("일반", "불공제")}


def _label(text: str) -> str:
    return (text or "").strip() or "(공란)"


def _state(row) -> str:
    """처리 상태 — 차변계정이 채워졌는가(담당자/위하고가 이미 손댄 행인가)."""
    if (row.전표상태 or "").strip() == "중복전표":
        return "중복전표"
    return "계정 채워짐" if (row.차변계정 or "").strip() else "계정 비어 있음"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="check_type_vs_nts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", nargs="?", default="~/kafa-out/_archive",
                    help="위하고 다운로드본(.xlsx)이 있는 폴더")
    args = ap.parse_args(argv)

    root = Path(args.archive).expanduser()
    files = sorted(p for p in root.rglob("*.xlsx") if not p.name.startswith("~$"))
    if not files:
        print(f"엑셀을 못 찾았습니다: {root}")
        return 2

    cross: dict[tuple, list] = defaultdict(lambda: [0, Decimal(0)])
    by_state: dict[str, Counter] = defaultdict(Counter)
    clients_off: set[str] = set()
    per_client: dict[str, list] = defaultdict(list)
    total = 0
    for f in files:
        try:
            rows = read_download_xlsx(f)
        except Exception as e:                       # noqa: BLE001
            print(f"  [건너뜀] 파일 하나를 읽지 못했습니다: {type(e).__name__}", file=sys.stderr)
            continue
        per_client[f.parent.name].extend(rows)
        for r in rows:
            t, n = _label(r.유형), _label(r.국세청)
            cell = cross[(t, n)]
            cell[0] += 1
            cell[1] += r.합계 or Decimal(0)
            agree = (t, n) in AGREE
            by_state[_state(r)]["같음" if agree else "어긋남"] += 1
            if not agree:
                clients_off.add(f.parent.name)
            total += 1

    types = sorted({t for t, _ in cross}, key=lambda x: (x == "(공란)", x))
    nts = sorted({n for _, n in cross}, key=lambda x: (x == "(공란)", x))
    print(f"파일 {len(files)}개 / 전표 {total:,}행\n")
    print("유형 × 국세청 (건수)")
    print(f"  {'유형':<10}" + "".join(f"{n:>12}" for n in nts))
    for t in types:
        print(f"  {t:<10}" + "".join(f"{cross[(t, n)][0]:>12,}" for n in nts))

    off = sum(c for s in by_state.values() for k, c in s.items() if k == "어긋남")
    print(f"\n유형과 국세청이 어긋나는 행: {off:,}건 ({off / max(total, 1) * 100:.1f}%)")
    print(f"  어긋난 행이 있는 수임처: {len(clients_off)}곳")
    print("\n처리 상태별")
    for state in ("계정 채워짐", "계정 비어 있음", "중복전표"):
        c = by_state.get(state)
        if not c:
            continue
        n = c["같음"] + c["어긋남"]
        print(f"  {state:<10} {n:>7,}행 중 어긋남 {c['어긋남']:>6,}건 "
              f"({c['어긋남'] / max(n, 1) * 100:.1f}%)")

    print("\n읽는 법")
    if off == 0:
        print("  유형이 국세청과 늘 같습니다 → 유형 칸은 국세청을 그대로 옮긴 기본값일 가능성이")
        print("  큽니다. 공제 기준을 유형으로 바꿔도 결과가 달라지지 않습니다.")
    else:
        filled = by_state.get("계정 채워짐", Counter())
        empty = by_state.get("계정 비어 있음", Counter())
        rate_f = filled["어긋남"] / max(sum(filled.values()), 1)
        rate_e = empty["어긋남"] / max(sum(empty.values()), 1)
        if rate_f > rate_e * 2 and filled["어긋남"]:
            print("  어긋남이 '계정 채워짐' 쪽에 몰려 있습니다 → 담당자가 유형을 고친 흔적으로")
            print("  보입니다. 공제 기준을 유형 칸으로 바꾸는 근거가 됩니다.")
        else:
            print("  어긋남이 처리 상태와 상관없이 퍼져 있습니다 → 누가 유형을 정했는지 이것만으로는")
            print("  가를 수 없습니다. 담당자께 여쭤봐야 합니다.")
    # 유형 추천 효과 — 처리 전 행을 같은 수임처·같은 가맹점의 처리 이력으로 채우면 몇 건이 바뀌나
    tset = type_settings()
    pending = changed = 0
    flips: Counter = Counter()
    for rows in per_client.values():
        idx = build_type_index_from_inputrows(rows)
        for r in rows:
            if (r.차변계정 or "").strip() or (r.전표상태 or "").strip() == "중복전표":
                continue
            pending += 1
            hit = idx.suggest(r.거래처, r.사업자등록번호, min_support=tset["min_support"],
                              min_share=tset["min_share"])
            if hit and hit.label != (r.유형 or "").strip():
                changed += 1
                flips[f"{_label(r.유형)} → {hit.label}"] += 1
    print(f"\n유형 추천 효과 (같은 가맹점 이력 {tset['min_support']}건 이상·"
          f"{tset['min_share']:.0%} 이상일 때만)")
    print(f"  처리 전 행 {pending:,}건 중 유형이 바뀌는 행: {changed:,}건")
    for k, n in flips.most_common():
        print(f"    {k:<12} {n:>6,}건")

    _account_sections(per_client)

    print("\n이 출력에는 수임처 이름이 없습니다. 그대로 붙여 주셔도 됩니다.")
    return 0


def _short(name: str) -> str:
    """'(판)접대비(기업업무추진비)' → '(판)접대비' — 표를 좁게."""
    return name.split("(기업업무")[0] if name else "(공란)"


def _account_sections(per_client: dict) -> None:
    """계정과 유형 — 처리된 행의 분포, 처리 전 행을 지금 도구로 분류한 결과."""
    from kafa.cli import classify_rows
    from kafa.config_loader import client_profile, load_account_codes
    from kafa.recommend.recommender import build_recommender
    from kafa.recommend.seed import build_seed_from_inputrows
    from kafa.rules.models import Deduct

    code_name: dict[int, str] = {}
    for name, code in load_account_codes().items():
        code_name.setdefault(int(code), name)

    # (1) 처리된 행: 계정별 유형
    done: dict[str, Counter] = defaultdict(Counter)
    for rows in per_client.values():
        for r in rows:
            acct = (r.차변계정 or "").strip()
            if acct and (r.전표상태 or "").strip() != "중복전표":
                done[_short(acct)][_label(r.유형)] += 1
    print("\n처리된 행의 계정별 유형 (건수 상위 12개)")
    print(f"  {'계정':<16}{'건수':>8}{'카과':>8}{'일반':>8}{'카과 비율':>10}")
    for acct, c in sorted(done.items(), key=lambda kv: -sum(kv[1].values()))[:12]:
        n = sum(c.values())
        print(f"  {acct:<16}{n:>8,}{c['카과']:>8,}{c['일반']:>8,}{c['카과'] / n:>10.0%}")
    ent = Counter()
    for acct, c in done.items():
        if ENTERTAINMENT in acct:
            ent.update(c)
    if ent:
        n = sum(ent.values())
        print(f"  → 접대비 계정 합계 {n:,}건 중 카과 {ent['카과']:,}건({ent['카과'] / n:.0%})")

    # (2) 처리 전 행: 지금 도구로 분류하면 (수임처마다 그 수임처 자료를 시드로)
    pend: dict[str, Counter] = defaultdict(Counter)
    for client, rows in per_client.items():
        seed = build_seed_from_inputrows(rows)
        classified, _ = classify_rows(
            rows, client_type=client_profile(client).get("client_type"), seed=seed,
            recommender=build_recommender(seed), profile=client_profile(client))
        for r, c in zip(rows, classified):
            if (r.차변계정 or "").strip() or c.skipped:
                continue
            name = _short(code_name.get(c.차변계정코드, "")) if c.차변계정코드 else "(미해소)"
            pend[name][c.공제여부.value if c.공제여부 else "(없음)"] += 1
    total = sum(sum(c.values()) for c in pend.values())
    print(f"\n처리 전 행 {total:,}건을 지금 도구로 분류하면 (추천 계정 상위 12개)")
    print(f"  {'계정':<16}{'건수':>8}{'공제':>8}{'불공제':>8}{'검토':>8}")
    for acct, c in sorted(pend.items(), key=lambda kv: -sum(kv[1].values()))[:12]:
        print(f"  {acct:<16}{sum(c.values()):>8,}{c[Deduct.DEDUCTIBLE.value]:>8,}"
              f"{c[Deduct.NON_DEDUCTIBLE.value]:>8,}{c[Deduct.REVIEW.value]:>8,}")
    wrong = sum(c[Deduct.DEDUCTIBLE.value] for a, c in pend.items() if ENTERTAINMENT in a)
    print(f"  → 접대비로 추천됐는데 공제로 나가는 처리 전 행: {wrong:,}건")


if __name__ == "__main__":
    raise SystemExit(main())
