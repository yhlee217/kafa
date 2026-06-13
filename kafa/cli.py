"""CLI — 입력 폴더(.xlsx) → 출력 폴더(.xls).

파이프라인: 읽기 → (배치 자가 시드) → Phase1 분류 → Phase2 미추천 추천
            → 방향반전 finalize → Phase3 .xls 생성(2MB 분할) → Phase4 검토 리포트.
원천 데이터는 로컬에서만 처리. 콘솔/리포트에는 마스킹 요약만.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kafa.config_loader import load_rules
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

    import json
    from datetime import datetime, timezone

    from kafa.io_wehago.reader import read_download_xlsx

    load_rules(args.config_dir)  # 설정 유효성 조기 검증
    in_path = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [in_path] if in_path.is_file() else sorted(in_path.glob("*.xlsx"))
    if not files:
        print(f"입력 .xlsx 없음: {in_path}", file=sys.stderr)
        return 1

    # 1차: 파일별로 읽기(에러 격리) → 정상 파일에서 자가 시드 구축
    per_file: dict[Path, list] = {}
    failures: dict[Path, str] = {}
    for f in files:
        try:
            per_file[f] = read_download_xlsx(f)
        except Exception as e:  # noqa: BLE001 — 배치 회복력(형식오류 포함)
            failures[f] = f"{type(e).__name__}: {e}"
            print(f"[{f.name}] 읽기 실패 → {failures[f]}", file=sys.stderr)

    seed = SeedIndex()
    for rows in per_file.values():
        s = build_seed_from_inputrows(rows, config_dir=args.config_dir)
        for k, c in s.by_vendor.items():
            seed.by_vendor.setdefault(k, type(c)()).update(c)
        for k, c in s.by_bizno.items():
            seed.by_bizno.setdefault(k, type(c)()).update(c)

    truth = None
    if args.truth:
        from kafa.eval import load_truth_csv
        truth = load_truth_csv(args.truth)

    dup = DupGuard(args.dup_store) if args.dup_store else None
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(),
                "files": [], "failures": {}}
    for f, rows in per_file.items():
        out = out_dir / (f.stem + "_upload.xls")
        try:
            res = process_rows(rows, out, client_type=args.client_type,
                               seed=seed, dup=dup, config_dir=args.config_dir)
        except Exception as e:  # noqa: BLE001 — 한 파일 실패가 배치를 막지 않음
            failures[f] = f"{type(e).__name__}: {e}"
            print(f"[{f.name}] 처리 실패 → {failures[f]}", file=sys.stderr)
            continue
        rep = res["report_obj"]
        parts = ", ".join(p.name for p in res["files"])
        print(f"[{f.name}] 작성 {res['written']} / 스킵 {res['skipped']} "
              f"/ 자동처리율 {rep.automation_rate:.1%} → {parts}")
        print(f"  검토: {res['report_path'].name} / 중간산출물: {res['csv_path'].name}")
        print(res["report"])

        entry = {"input": f.name, "written": res["written"],
                 "skipped": res["skipped"], "automation_rate": rep.automation_rate,
                 "outputs": [p.name for p in res["files"]],
                 "review": res["report_path"].name, "csv": res["csv_path"].name}

        if truth is not None:
            from kafa.eval import evaluate, render_eval
            ev = evaluate(res["classified"], truth)
            acc_text = render_eval(ev)
            print(acc_text)
            acc_path = out.with_name(out.stem + "_accuracy.txt")
            acc_path.write_text(acc_text, encoding="utf-8")
            entry["accuracy"] = ev.overall_accuracy
            entry["accuracy_report"] = acc_path.name
        manifest["files"].append(entry)

    manifest["failures"] = {f.name: msg for f, msg in failures.items()}
    (out_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
