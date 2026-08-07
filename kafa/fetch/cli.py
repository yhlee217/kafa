"""kafa-fetch — 사람이 로그인한 브라우저를 이어받아 다운로드 반복을 자동화.

사용:
  kafa-fetch --inbox C:\\kafa\\inbox --clients 고객목록.txt --months 6
  kafa-fetch --inbox ... --clients A,B,C --from 2025-09 --to 2026-02
  kafa-fetch --inspect            # 현재 화면의 후보 요소를 뽑아 selector 보정에 사용
  kafa-fetch ... --dry-run        # 무엇을 받을지 목록만 확인(브라우저 안 띄움)

로그인은 **사람이 직접** 한다(아이디·비밀번호·인증서·OTP 를 코드가 다루지 않음).
스크립트는 로그인 완료를 기다렸다가 화면 조작만 이어서 한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOS_NOTICE = """\
[확인] 이 도구는 사람이 로그인한 브라우저에서 '반복 클릭'만 대신합니다.
       - 아이디/비밀번호/공동인증서/OTP 를 코드가 다루지 않습니다.
       - 서비스 이용약관이 자동화(스크립트) 접근을 금지한다면 사용하지 마세요.
         약관 확인은 사용자 책임입니다.
"""


def _clients_from(arg: str) -> list[str]:
    """쉼표 목록 또는 파일 경로(한 줄에 하나)."""
    p = Path(arg)
    if p.exists():
        return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")]
    return [c.strip() for c in arg.split(",") if c.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kafa-fetch",
        description="감독형 수집 — 로그인은 사람이, 반복 다운로드는 자동으로")
    ap.add_argument("--inbox", required=False, help="받은 파일을 둘 폴더(파이프라인 inbox)")
    ap.add_argument("--clients", help="거래처 목록: 쉼표 구분 또는 파일 경로")
    ap.add_argument("--months", type=int, help="최근 N개월")
    ap.add_argument("--from", dest="frm", help="시작 기간 YYYY-MM")
    ap.add_argument("--to", dest="to", help="종료 기간 YYYY-MM")
    ap.add_argument("--archive", help="처리 완료 보관 폴더(out/_archive) — 있으면 재수집 생략")
    ap.add_argument("--config", help="화면 설정 파일(기본 config/fetch/wehago.yaml)")
    ap.add_argument("--profile", help="브라우저 프로필 폴더(로그인 유지)")
    ap.add_argument("--attach-port", type=int,
                    help="이미 띄운 크롬에 붙기(--remote-debugging-port 값)")
    ap.add_argument("--dry-run", action="store_true", help="받을 목록만 출력")
    ap.add_argument("--inspect", action="store_true",
                    help="현재 화면의 후보 요소를 출력(selector 보정용)")
    args = ap.parse_args(argv)

    from kafa.fetch.plan import build_plan, months_between, recent_months
    from kafa.fetch.wehago import (NotCalibrated, load_fetch_config,
                                   missing_selectors, run_fetch)

    cfg = load_fetch_config(args.config)

    if args.inspect:
        from kafa.fetch.inspect import inspect_page
        from kafa.fetch.session import browser_page, wait_for_human
        print(TOS_NOTICE)
        with browser_page(profile_dir=args.profile,
                          attach_port=args.attach_port) as page:
            wait_for_human("브라우저에서 로그인하고, 보정할 화면을 열어 두세요.\n"
                           + str(cfg.get("start_hint", "")))
            for line in inspect_page(page):
                print(line)
        return 0

    if not args.inbox or not args.clients:
        ap.error("--inbox 와 --clients 가 필요합니다(또는 --inspect 사용).")

    clients = _clients_from(args.clients)
    if args.frm and args.to:
        periods = months_between(args.frm, args.to)
    elif args.months:
        periods = recent_months(args.months)
    else:
        ap.error("--months 또는 --from/--to 로 기간을 지정하세요.")

    plan = build_plan(args.inbox, clients, periods, archive=args.archive)
    print(f"거래처 {len(clients)}곳 × 기간 {len(periods)}개월 = {plan.total}건")
    print(f"  받을 것 {len(plan.tasks)}건 / 이미 있음 {len(plan.skipped)}건")

    if args.dry_run:
        for t in plan.tasks[:40]:
            print(f"   - {t.client}/{t.period}")
        if len(plan.tasks) > 40:
            print(f"   … 외 {len(plan.tasks) - 40}건")
        return 0

    miss = missing_selectors(cfg)
    if miss:
        print("\n[중단] 화면 selector 가 아직 보정되지 않았습니다: " + ", ".join(miss),
              file=sys.stderr)
        print("       `kafa-fetch --inspect` 로 화면을 확인해 "
              "config/fetch/wehago.yaml 을 채운 뒤 다시 실행하세요.", file=sys.stderr)
        return 2

    if not plan.tasks:
        print("받을 것이 없습니다(모두 수집됨).")
        return 0

    from kafa.fetch.session import browser_page, wait_for_human
    print(TOS_NOTICE)
    with browser_page(profile_dir=args.profile, attach_port=args.attach_port,
                      downloads_dir=args.inbox) as page:
        wait_for_human("브라우저에서 로그인해 주세요.\n" + str(cfg.get("start_hint", "")))
        try:
            res = run_fetch(page, plan, args.inbox, cfg=cfg,
                            on_progress=lambda t, s: print(f"  [{s}] {t.client}/{t.period}"))
        except NotCalibrated as e:
            print(f"\n[중단] {e}", file=sys.stderr)
            return 2

    print(f"\n저장 {len(res.saved)}건 / 실패 {len(res.failures)}건 / 생략 {res.skipped}건")
    for k, v in res.failures.items():
        print(f"  [실패] {k} → {v}", file=sys.stderr)
    print(f"\n다음: kafa-pipeline {args.inbox} <출력폴더>")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
