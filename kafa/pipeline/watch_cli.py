"""kafa-watch — inbox 디렉토리를 상시 감시해 새 파일을 자동 처리.

사용:  kafa-watch <inbox> <output> [--interval 10] [--client-type ...]
켜두면 inbox 에 위하고 다운로드본이 들어올 때마다 자동으로 처리하고 알림을 띄운다.
종료는 Ctrl+C. (한 번만 처리하려면 kafa-pipeline 사용.)
"""
from __future__ import annotations

import argparse

from kafa.pipeline.watch import watch


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="kafa-watch",
        description="inbox 를 상시 감시 — 새 .xlsx 가 들어오면 자동 처리(Ctrl+C 종료)")
    p.add_argument("inbox", help="다운로드본(.xlsx)을 넣는 디렉토리(고객별 하위폴더 권장)")
    p.add_argument("output", help="결과/아카이브/DB 출력 디렉토리")
    p.add_argument("--interval", type=float, default=10.0, help="스캔 주기(초, 기본 10)")
    p.add_argument("--client-type", choices=["corporate", "individual"], default=None)
    p.add_argument("--config-dir", default=None)
    args = p.parse_args(argv)

    print(f"[kafa-watch] 감시 시작: {args.inbox}  (주기 {args.interval}s · 종료 Ctrl+C)")
    try:
        watch(args.inbox, args.output, interval=args.interval,
              client_type=args.client_type, config_dir=args.config_dir)
    except KeyboardInterrupt:
        print("\n[kafa-watch] 종료했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
