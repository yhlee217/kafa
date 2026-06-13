"""CLI — 입력 폴더(.xlsx) → 출력 폴더(.xls).

파이프라인: 읽기 → (배치 자가 시드) → Phase1 분류 → Phase2 미추천 추천
            → 방향반전 finalize → Phase3 .xls 생성(2MB 분할) → Phase4 검토 리포트.
원천 데이터는 로컬에서만 처리. 콘솔/리포트에는 마스킹 요약만.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kafa.dup_guard import DupGuard, make_key
from kafa.recommend.recommender import recommend_account
from kafa.recommend.seed import SeedIndex, build_seed_from_inputrows
from kafa.rules.engine import classify_row, finalize_reversal
from kafa.rules.models import InputRow, Verdict


def process_rows(rows: list[InputRow], out_path: Path, *,
                 client_type: str | None = None,
                 seed: SeedIndex | None = None,
                 dup: DupGuard | None = None,
                 config_dir: str | None = None) -> dict:
    from kafa.io_wehago.writer import to_output_row, write_upload_xls
    from kafa.report.review import build_review, render_report, write_review_csv

    classified = []
    skipped = 0
    for row in rows:
        c = classify_row(row, client_type=client_type, config_dir=config_dir)
        if c.skipped:                       # 1.8 중복전표 등 — 집계용으로 보존
            skipped += 1
            classified.append(c)
            continue
        # 2차 중복 가드
        if dup is not None:
            key = make_key(f"{row.연도}-{row.일자}", row.거래처,
                           row.사업자등록번호, row.합계)
            if dup.is_duplicate(key):
                c.skipped = True
                c.skip_reason = "dup_guard(2차)"
                skipped += 1
                classified.append(c)
                continue
            dup.record(key)
        # Phase 2: 미추천 해소
        if c.판정유형 == Verdict.UNRESOLVED and c.차변계정코드 is None:
            rec = recommend_account(row, seed, config_dir=config_dir)
            if rec.resolved:
                c.차변계정코드 = rec.account_code
                c.판정유형 = Verdict.RECOMMENDED
                c.신뢰도 = rec.confidence
                c.추천근거 = rec.basis
                c.add_rule("RECO-001")
        finalize_reversal(c)
        classified.append(c)

    if dup is not None:
        dup.flush()

    rep = build_review(classified, config_dir=config_dir)
    out_rows = [to_output_row(c, config_dir=config_dir)
                for c in classified if not c.skipped]
    files = write_upload_xls(out_rows, out_path, strict=False, config_dir=config_dir)

    # 검토 리포트(.txt) + 중간 산출물 CSV(담당자 전용) 산출
    report_text = render_report(rep)
    report_path = out_path.with_name(out_path.stem + "_review.txt")
    report_path.write_text(report_text, encoding="utf-8")
    csv_path = out_path.with_name(out_path.stem + "_review.csv")
    write_review_csv(classified, csv_path)

    return {"report_obj": rep, "report": report_text,
            "report_path": report_path, "csv_path": csv_path,
            "classified": classified,
            "skipped": skipped, "written": len(out_rows), "files": files}


def process_file(in_path: Path, out_path: Path, *,
                 seed: SeedIndex | None = None, **kw) -> dict:
    from kafa.io_wehago.reader import read_download_xlsx
    rows = read_download_xlsx(in_path)
    if seed is None:
        seed = build_seed_from_inputrows(rows, config_dir=kw.get("config_dir"))
    return process_rows(rows, out_path, seed=seed, **kw)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kafa",
                                description="위하고 신용카드 매입 전표 분류·생성")
    p.add_argument("input", help="입력 폴더 또는 .xlsx 파일")
    p.add_argument("output", help="출력 폴더")
    p.add_argument("--client-type", choices=["corporate", "individual"],
                   default=None, help="기장 클라이언트 유형(기본=config)")
    p.add_argument("--dup-store", default=None, help="중복 가드 JSON 경로")
    p.add_argument("--truth", default=None,
                   help="담당자 정답 CSV(수작업 대조) — 정확도 검증")
    p.add_argument("--config-dir", default=None)
    args = p.parse_args(argv)

    from kafa.report.review import render_report
    from kafa.service import run_batch

    batch = run_batch(args.input, args.output, client_type=args.client_type,
                      dup_store=args.dup_store, truth=args.truth,
                      config_dir=args.config_dir)

    for name, msg in batch.failures.items():
        print(f"[{name}] 실패 → {msg}", file=sys.stderr)

    for fr in batch.files:
        parts = ", ".join(fr.output_files)
        print(f"[{fr.input_name}] 작성 {fr.written} / 스킵 {fr.skipped} "
              f"/ 자동처리율 {fr.automation_rate:.1%} → {parts}")
        print(f"  검토: {fr.review_path} / 중간산출물: {fr.csv_path}")
        print(render_report(fr.report_obj))
        if fr.accuracy_text:
            print(fr.accuracy_text)

    if batch.manifest_path:
        print(f"\n매니페스트: {batch.manifest_path}")
    return 0 if batch.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
