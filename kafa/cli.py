"""CLI — 입력 폴더(.xlsx) → 출력 폴더(.xls).

파이프라인: 읽기 → Phase1 분류 → Phase2 미추천 추천 → 방향반전 finalize
            → Phase3 .xls 생성 → Phase4 검토 리포트(담당자 전용).
원천 데이터는 로컬에서만 처리. 콘솔/리포트에는 마스킹 요약만.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kafa.config_loader import load_rules
from kafa.dup_guard import DupGuard, make_key
from kafa.recommend.recommender import recommend_account
from kafa.recommend.seed import SeedIndex
from kafa.rules.engine import classify_row, finalize_reversal
from kafa.rules.models import Verdict


def process_file(in_path: Path, out_path: Path, *,
                 client_type: str | None = None,
                 seed: SeedIndex | None = None,
                 dup: DupGuard | None = None,
                 config_dir: str | None = None) -> dict:
    # 지연 import: 룰/리포트는 pandas/xlwt 불필요
    from kafa.io_wehago.reader import read_download_xlsx
    from kafa.io_wehago.writer import to_output_row, write_upload_xls
    from kafa.report.review import build_summary, render_text

    rows = read_download_xlsx(in_path)
    classified = []
    skipped = 0
    for row in rows:
        c = classify_row(row, client_type=client_type, config_dir=config_dir)
        if c.skipped:
            skipped += 1
            continue
        # 2차 중복 가드
        if dup is not None:
            key = make_key(f"{row.연도}-{row.일자}", row.거래처,
                           row.사업자등록번호, row.합계)
            if dup.is_duplicate(key):
                c.skipped = True
                c.skip_reason = "dup_guard(2차)"
                skipped += 1
                continue
            dup.record(key)
        # Phase 2: 미추천 해소
        if c.판정유형 == Verdict.UNRESOLVED and c.차변계정코드 is None:
            rec = recommend_account(row, seed, config_dir=config_dir)
            if rec.resolved:
                c.차변계정코드 = rec.account_code
                c.판정유형 = Verdict.RECOMMENDED
                c.신뢰도 = rec.confidence
                c.add_rule("RECO-001")
        finalize_reversal(c)
        classified.append(c)

    summary = build_summary(classified)
    if dup is not None:
        dup.flush()

    # Phase 3 출력
    out_rows = [to_output_row(c) for c in classified if not c.skipped]
    write_upload_xls(out_rows, out_path, strict=False)

    return {"summary": summary, "report": render_text(summary),
            "skipped": skipped, "written": len(out_rows)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kafa",
                                description="위하고 신용카드 매입 전표 분류·생성")
    p.add_argument("input", help="입력 폴더 또는 .xlsx 파일")
    p.add_argument("output", help="출력 폴더")
    p.add_argument("--client-type", choices=["corporate", "individual"],
                   default=None, help="기장 클라이언트 유형(기본=config)")
    p.add_argument("--dup-store", default=None, help="중복 가드 JSON 경로")
    p.add_argument("--config-dir", default=None)
    args = p.parse_args(argv)

    load_rules(args.config_dir)  # 설정 유효성 조기 검증
    in_path = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [in_path] if in_path.is_file() else sorted(in_path.glob("*.xlsx"))
    if not files:
        print(f"입력 .xlsx 없음: {in_path}", file=sys.stderr)
        return 1

    dup = DupGuard(args.dup_store) if args.dup_store else None
    for f in files:
        out = out_dir / (f.stem + "_upload.xls")
        res = process_file(f, out, client_type=args.client_type,
                           dup=dup, config_dir=args.config_dir)
        print(f"[{f.name}] 작성 {res['written']} / 스킵 {res['skipped']}")
        print(res["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
