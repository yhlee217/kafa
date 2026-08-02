"""kafa-learn — 처리 이력에서 규칙을 추정한다.

사용:
    kafa-learn <폴더|파일.xlsx> [--out 리포트.txt] [--proposal 제안.yaml]
    kafa-learn out/_archive            # 파이프라인이 보관한 원본 전체로 학습

여러 파일·여러 달을 한 번에 넣을수록 근거가 두터워진다. 결과는 **추정**이며
자동 반영하지 않는다(담당자 확인용). 리포트에 거래처 실명·사업자번호는 없다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _iter_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.xlsx") if not p.name.startswith("~$"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kafa-learn",
        description="위하고 처리 이력에서 보류 규칙을 추정(자동 반영 안 함)")
    ap.add_argument("target", help="다운로드본(.xlsx) 파일 또는 폴더")
    ap.add_argument("--out", default=None, help="리포트 저장 경로(.txt)")
    ap.add_argument("--proposal", default=None, help="설정 제안 저장 경로(.yaml)")
    ap.add_argument("--min-support", type=int, default=5,
                    help="업종 규칙 최소 근거 건수(기본 5)")
    ap.add_argument("--min-ratio", type=float, default=0.8,
                    help="업종 규칙 최소 편중 비율(기본 0.8)")
    ap.add_argument("--config-dir", default=None)
    args = ap.parse_args(argv)

    from kafa.io_wehago.reader import read_download_xlsx
    from kafa.learn.infer import infer_rules, propose_config, render_inference

    files = _iter_files(Path(args.target))
    if not files:
        print(f"학습할 .xlsx 가 없습니다: {args.target}", file=sys.stderr)
        return 1

    rows, failed = [], 0
    for f in files:
        try:
            rows.extend(read_download_xlsx(f))
        except Exception as e:  # noqa: BLE001 — 형식 오류 파일은 건너뛰고 계속
            failed += 1
            print(f"[건너뜀] {f.name}: {type(e).__name__}: {e}", file=sys.stderr)

    if not rows:
        print("읽을 수 있는 행이 없습니다.", file=sys.stderr)
        return 1

    rep = infer_rules(rows, config_dir=args.config_dir,
                      min_support=args.min_support, min_ratio=args.min_ratio)
    text = render_inference(rep)
    print(f"파일 {len(files) - failed}개 학습" + (f" (실패 {failed}개)" if failed else ""))
    print(text)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n리포트: {args.out}")
    if args.proposal:
        import yaml
        Path(args.proposal).write_text(
            yaml.safe_dump(propose_config(rep), allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        print(f"설정 제안: {args.proposal}  (검토 후 반영하세요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
