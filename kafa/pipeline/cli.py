"""kafa-pipeline — inbox 디렉토리 일괄 처리 CLI.

사용:  kafa-pipeline <inbox> <output> [--client-type corporate|individual] [--config-dir ...]
inbox 에 위하고 다운로드본(.xlsx)을 고객별 하위폴더로 모아두면, 업로드용 .xls·리포트를
출력 폴더에 만들고 SQLite(kafa.db)에 누적한다. 처리한 원본은 _archive 로 이동.
"""
from __future__ import annotations

import argparse
import sys

from kafa.pipeline.runner import run_pipeline


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="kafa-pipeline",
        description="inbox 디렉토리의 위하고 다운로드본을 일괄 처리(업로드 산출물 + 로컬 DB 누적)")
    p.add_argument("inbox", help="다운로드본(.xlsx) 디렉토리(고객별 하위폴더 권장)")
    p.add_argument("output", help="결과/아카이브/DB 출력 디렉토리")
    p.add_argument("--client-type", choices=["corporate", "individual"], default=None)
    p.add_argument("--config-dir", default=None)
    args = p.parse_args(argv)

    res = run_pipeline(args.inbox, args.output,
                       client_type=args.client_type, config_dir=args.config_dir)

    for o in res.outcomes:
        print(f"[{o.client}/{o.period}] {o.file} → 작성 {o.written}/스킵 {o.skipped} "
              f"(DB 신규 {o.inserted}/기존 {o.existing})")
    for name, msg in res.failures.items():
        print(f"[실패] {name} → {msg}", file=sys.stderr)
    print(f"\nDB: {res.db_path} (총 {res.total_in_db}건 누적)")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
