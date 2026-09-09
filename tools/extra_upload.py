"""빠져 있던 전표만 모아 업로드본을 따로 만든다 (2026-09-06 키 충돌 수정의 보충분).

같은 날 같은 가맹점 같은 금액의 두 번째 행이 예전에 DB·업로드본에서 사라졌다
(docs/domain_notes.md). 이미 업로드를 마친 뒤라면 전체를 다시 올릴 수 없다 —
그러면 이중계상이 된다. 그래서 **빠졌던 행만** 뽑아 보충 업로드본을 만든다.

읽기 전용이다. DB 도 dup.json 도 건드리지 않는다.

    python tools/extra_upload.py ~/kafa-out/_archive ~/kafa-보충 [--db ~/kafa-out/kafa.db]
"""
from __future__ import annotations

import argparse
import csv
import warnings

# 위하고 다운로드본은 기본 스타일이 없어 openpyxl 이 파일마다 경고를 낸다 — 출력만 가린다.
warnings.filterwarnings("ignore", message="Workbook contains no default style",
                        module="openpyxl")
from collections import Counter, defaultdict
from pathlib import Path

from kafa.cli import classify_rows
from kafa.config_loader import client_profile
from kafa.io_wehago.reader import read_download_xlsx
from kafa.io_wehago.writer import to_output_row, write_upload_xls
from kafa.pipeline.runner import _merge_seed, _period_of, resolve_client
from kafa.recommend.recommender import build_recommender
from kafa.recommend.seed import build_seed_from_inputrows
from kafa.rules.models import Verdict


def _key(r) -> tuple:
    return (f"{r.연도}-{r.일자}", r.거래처, r.사업자등록번호, str(r.합계), r.품명)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="extra_upload", description=__doc__)
    ap.add_argument("archive", help="원본이 있는 폴더(예: ~/kafa-out/_archive)")
    ap.add_argument("out_dir", help="보충 업로드본을 낼 폴더")
    ap.add_argument("--db", help="누적 DB(추천 이력 재료). 없어도 된다")
    ap.add_argument("--skip-unresolved", action="store_true",
                    help="차변계정이 비어 있는 행을 업로드본에서 빼고 "
                         "미해소_수기입력.csv 로 따로 낸다")
    ap.add_argument("--config-dir")
    args = ap.parse_args(argv)

    root = Path(args.archive).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    files = sorted(p for p in root.rglob("*.xlsx") if not p.name.startswith("~$"))
    if not files:
        print(f"엑셀을 못 찾았습니다: {root}")
        return 2

    store = None
    if args.db:
        from kafa.store.db import VoucherStore
        store = VoucherStore(Path(args.db).expanduser())

    총 = 0
    합계금액 = 0
    per_client: dict[str, int] = defaultdict(int)
    made: list[Path] = []
    판정 = Counter()
    검토 = 0
    미해소목록: list[tuple] = []
    겹친조합: Counter = Counter()
    목록: list[tuple] = []
    try:
        for f in files:
            client = resolve_client(root, f)
            try:
                rows = read_download_xlsx(f)
            except Exception as e:                       # noqa: BLE001
                print(f"  [건너뜀] {f.name}: {type(e).__name__}: {e}")
                continue

            seen: Counter = Counter()
            빠진행 = []
            for r in rows:
                k = _key(r)
                if seen[k]:
                    빠진행.append(r)
                    겹친조합[(client, r.거래처, str(r.합계))] += 1
                seen[k] += 1
            if not 빠진행:
                continue

            # 추천 재료는 그 파일 전체 + 이 수임처의 누적 이력(빠진 행만으로는 근거가 얇다)
            seed = build_seed_from_inputrows(rows, config_dir=args.config_dir)
            if store is not None:
                from kafa.recommend.seed import build_seed_index
                _merge_seed(seed, build_seed_index(store.seed_records(client)))
            profile = client_profile(client, args.config_dir)
            classified, _ = classify_rows(
                빠진행, client_type=profile.get("client_type"), seed=seed,
                recommender=build_recommender(seed, config_dir=args.config_dir),
                profile=profile, config_dir=args.config_dir)

            보류: list = []
            for c in classified:
                if c.skipped:
                    continue
                판정[c.판정유형] += 1
                if args.skip_unresolved and c.차변계정코드 is None:
                    보류.append(c)
                    src = c.source
                    미해소목록.append((
                        client, f"{src.연도}-{src.일자}" if src else "",
                        src.거래처 if src else "", src.사업자등록번호 if src else "",
                        src.품명 if src else "", src.업태 if src else "",
                        src.종목 if src else "",
                        src.공급가액 if src else "", src.세액 if src else "",
                        src.비과세 if src else "", src.합계 if src else "",
                        c.유형코드 or "", c.대변계정코드 or "",
                        {"deductible": "공제", "non_deductible": "불공제",
                         "review": "검토"}.get(
                             c.공제여부.value if c.공제여부 else "", "")))
                검토 += 1 if c.needs_review else 0
                src = c.source
                목록.append((client, f"{src.연도}-{src.일자}" if src else "",
                            src.거래처 if src else "", src.합계 if src else "",
                            src.품명 if src else "", c.차변계정코드 or "",
                            c.판정유형.value if c.판정유형 else "",
                            "예" if c.needs_review else ""))

            뺀행 = set(id(x) for x in 보류)
            보낼행 = [to_output_row(c, config_dir=args.config_dir)
                    for c in classified
                    if not c.skipped and id(c) not in 뺀행]
            if not 보낼행:
                continue
            period = _period_of(rows)
            path = out_dir / client / f"{f.stem}_{period}_보충_upload.xls"
            made += write_upload_xls(보낼행, path, strict=False,
                                     config_dir=args.config_dir)
            총 += len(보낼행)
            per_client[client] += len(보낼행)
            for c in classified:
                if c.skipped or id(c) in 뺀행 or c.source is None:
                    continue
                try:
                    합계금액 += int(c.source.합계)
                except (TypeError, ValueError):
                    pass
    finally:
        if store is not None:
            store.close()

    print(f"보충 업로드본: {len(made)}개 파일 / 전표 {총}건 / {합계금액:,}원")
    for name, n in sorted(per_client.items(), key=lambda kv: -kv[1]):
        print(f"  {name}: {n}건")

    확정 = 판정.get(Verdict.RULE_CONFIRMED, 0)
    추천 = 판정.get(Verdict.RECOMMENDED, 0)
    미해소 = 판정.get(Verdict.UNRESOLVED, 0)
    뽑은전체 = 확정 + 추천 + 미해소
    if 뽑은전체:
        print(f"\n계정 상태(뽑아낸 {뽑은전체}건 기준): 룰확정 {확정} / 추천해소 {추천}"
              f" / 미해소 {미해소}  (자동처리 {(확정 + 추천) / 뽑은전체 * 100:.1f}%)")
        if 미해소 and not args.skip_unresolved:
            print(f"  ※ 미해소 {미해소}건은 계정이 비어 있습니다 — 올리기 전에 채우세요.")
            print("     (--skip-unresolved 를 주면 빼고 만들고, 따로 CSV 로 냅니다)")
        if 검토:
            print(f"  ※ 검토 플래그 {검토}건")

        print("\n같은 값이 여러 번 나온 조합(진짜 별개 거래인지 확인하세요):")
        for (cl, 거래처, 금액), n in 겹친조합.most_common(6):
            print(f"  {cl} | {거래처[:26]} | {int(float(금액)):,}원 → 추가 {n}건")

        목록_path = out_dir / "보충목록.csv"
        목록_path.parent.mkdir(parents=True, exist_ok=True)
        with 목록_path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["수임처", "거래일자", "거래처", "합계", "품명",
                        "차변계정코드", "판정", "검토"])
            w.writerows(목록)
        print(f"\n  전체 목록(눈으로 확인용): {목록_path}")

    if 미해소목록:
        path = out_dir / "미해소_수기입력.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["수임처", "거래일자", "거래처", "사업자번호", "품명", "업태", "종목",
                        "공급가액", "세액", "비과세", "합계", "유형코드", "대변계정코드",
                        "공제여부"])
            w.writerows(미해소목록)
        print(f"\n업로드본에서 뺀 미해소 {len(미해소목록)}건: {path}")
        print("  이 건들은 계정이 비어 있어 뺐습니다 — 위하고에 직접 넣으세요.")

    if made:
        print(f"  → {out_dir}")
        print("  이미 올린 업로드본은 그대로 두고, 이 파일만 추가로 올리면 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
