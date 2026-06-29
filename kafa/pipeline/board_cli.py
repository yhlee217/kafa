"""kafa-board — 고객 진행 현황 보드 출력(텍스트) + HTML 생성.

사용:  kafa-board <output>     (output = 파이프라인 출력 디렉토리, kafa.db 위치)
파이프라인 실행 시 _board.html 은 자동 갱신되지만, 언제든 이 명령으로 다시 볼 수 있다.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kafa-board", description="고객 진행 현황 보드")
    ap.add_argument("output", help="파이프라인 출력 디렉토리(kafa.db 위치)")
    args = ap.parse_args(argv)

    from kafa.pipeline.summary import build_board, render_board_text, write_board
    from kafa.store.db import VoucherStore

    db_path = Path(args.output) / "kafa.db"
    if not db_path.exists():
        print("아직 처리된 데이터가 없습니다(kafa.db 없음).")
        return 1
    with VoucherStore(db_path) as db:
        print(render_board_text(build_board(db)))
    p = write_board(args.output)
    print(f"\nHTML 보드: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
