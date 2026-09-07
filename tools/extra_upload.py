"""빠져 있던 전표만 모아 업로드본을 따로 만든다 (2026-09-06 키 충돌 수정의 보충분).

같은 날 같은 가맹점 같은 금액의 두 번째 행이 예전에 DB·업로드본에서 사라졌다
(docs/domain_notes.md). 이미 업로드를 마친 뒤라면 전체를 다시 올릴 수 없다 —
그러면 이중계상이 된다. 그래서 **빠졌던 행만** 뽑아 보충 업로드본을 만든다.

읽기 전용이다. DB 도 dup.json 도 건드리지 않는다.

    python tools/extra_upload.py ~/kafa-out/_archive ~/kafa-보충 [--db ~/kafa-out/kafa.db]
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from kafa.cli import classify_rows
from kafa.config_loader import client_profile
from kafa.io_wehago.reader import read_download_xlsx
from kafa.io_wehago.writer import to_output_row, write_upload_xls
from kafa.pipeline.runner import _merge_seed, _period_of, resolve_client
from kafa.recommend.recommender import build_recommender
from kafa.recommend.seed import build_seed_from_inputrows


def _key(r) -> tuple:
    return (f"{r.연도}-{r.일자}", r.거래처, r.사업자등록번호, str(r.합계), r.품명)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="extra_upload", description=__doc__)
    ap.add_argument("archive", help="원본이 있는 폴더(예: ~/kafa-out/_archive)")
    ap.add_argument("out_dir", help="보충 업로드본을 낼 폴더")
    ap.add_argument("--db", help="누적 DB(추천 이력 재료). 없어도 된다")
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

            보낼행 = [to_output_row(c, config_dir=args.config_dir)
                    for c in classified if not c.skipped]
            if not 보낼행:
                continue
            period = _period_of(rows)
            path = out_dir / client / f"{f.stem}_{period}_보충_upload.xls"
            made += write_upload_xls(보낼행, path, strict=False,
                                     config_dir=args.config_dir)
            총 += len(보낼행)
            per_client[client] += len(보낼행)
            for c in classified:
                if not c.skipped and c.source is not None:
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
    if made:
        print(f"\n  → {out_dir}")
        print("  이미 올린 업로드본은 그대로 두고, 이 파일만 추가로 올리면 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
