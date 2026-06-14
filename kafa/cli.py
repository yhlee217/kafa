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
from kafa.recommend.recommender import Recommender, SeedRecommender
from kafa.recommend.seed import SeedIndex, build_seed_from_inputrows
from kafa.rules.engine import classify_row, finalize_reversal
from kafa.rules.models import InputRow, Verdict


def classify_rows(rows: list[InputRow], *,
                  client_type: str | None = None,
                  seed: SeedIndex | None = None,
                  recommender: Recommender | None = None,
                  dup: DupGuard | None = None,
                  config_dir: str | None = None) -> tuple[list, int]:
    """행 목록 → (ClassifiedRow 목록, 스킵 수). 파일은 쓰지 않는다.

    미추천 행은 recommender 로 해소(없으면 시드 기반). 스킵 행도 집계용으로 포함.
    """
    if recommender is None:
        recommender = SeedRecommender(seed, config_dir=config_dir)

    classified = []
    skipped = 0
    for row in rows:
        c = classify_row(row, client_type=client_type, config_dir=config_dir)
        if c.skipped:                       # 1.8 중복전표 등 — 집계용으로 보존
            skipped += 1
            classified.append(c)
            continue
        if dup is not None:                 # 2차 중복 가드
            key = make_key(f"{row.연도}-{row.일자}", row.거래처,
                           row.사업자등록번호, row.합계)
            if dup.is_duplicate(key):
                c.skipped = True
                c.skip_reason = "dup_guard(2차)"
                skipped += 1
                classified.append(c)
                continue
            dup.record(key)
        # Phase 2: 미추천 해소 (호스트 Claude 추정 / 시드 — recommender 가 결정)
        if c.판정유형 == Verdict.UNRESOLVED and c.차변계정코드 is None:
            rec = recommender.recommend_for(row)
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
    return classified, skipped


def process_rows(rows: list[InputRow], out_path: Path, *,
                 client_type: str | None = None,
                 seed: SeedIndex | None = None,
                 recommender: Recommender | None = None,
                 dup: DupGuard | None = None,
                 config_dir: str | None = None) -> dict:
    from kafa.io_wehago.writer import to_output_row, write_upload_xls
    from kafa.report.client_report import build_client_report
    from kafa.report.evidence_check import build_evidence_check, render_evidence_check
    from kafa.report.review import build_review, render_report, write_review_csv
    from kafa.report.vat_summary import (
        build_vat_summary,
        render_vat_summary,
        write_vat_summary_csv,
    )

    classified, skipped = classify_rows(
        rows, client_type=client_type, seed=seed, recommender=recommender,
        dup=dup, config_dir=config_dir)

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

    # 부가세 신고 보조 집계(세무대리인 서비스)
    vat = build_vat_summary(classified)
    vat_path = out_path.with_name(out_path.stem + "_vat.txt")
    vat_path.write_text(render_vat_summary(vat), encoding="utf-8")
    write_vat_summary_csv(vat, out_path.with_name(out_path.stem + "_vat.csv"))

    # 고객 제공용 요약 리포트(세무대리인 → 고객)
    client_path = out_path.with_name(out_path.stem + "_client.txt")
    client_path.write_text(build_client_report(classified, config_dir=config_dir),
                           encoding="utf-8")

    # 증빙·리스크 점검(세무대리인)
    evid = build_evidence_check(classified)
    risk_path = out_path.with_name(out_path.stem + "_risk.txt")
    risk_path.write_text(render_evidence_check(evid), encoding="utf-8")

    return {"report_obj": rep, "report": report_text,
            "report_path": report_path, "csv_path": csv_path,
            "vat": vat, "vat_path": vat_path, "client_path": client_path,
            "evid": evid, "risk_path": risk_path,
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
