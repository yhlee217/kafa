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


def main(argv: list[str] | None = None, *, input_fn=input) -> int:
    ap = argparse.ArgumentParser(
        prog="kafa-fetch",
        description="감독형 수집 — 로그인은 사람이, 반복 다운로드는 자동으로")
    ap.add_argument("--inbox", required=False, help="받은 파일을 둘 폴더(파이프라인 inbox)")
    ap.add_argument("--clients", help="수임처 목록: 쉼표 구분 또는 파일 경로")
    ap.add_argument("--master",
                    help="수임처 마스터 엑셀(회사명+접속 URL). 주면 화면 검색 없이 "
                         "주소로 바로 이동하고, --clients 를 생략하면 전체를 대상으로 함")
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
    ap.add_argument("--inspect-out", help="보정용 출력 저장 경로(기본 kafa-inspect.txt)")
    ap.add_argument("--goto",
                    help="--inspect 시 로그인 후 이 주소로 이동한 뒤 살펴본다"
                         "(수임처 마스터의 '접속 URL' 하나를 넣으면 신용카드 화면으로 바로 감)")
    ap.add_argument("--record", action="store_true",
                    help="--inspect 시 사람이 직접 한 번 받는 동안 클릭·입력 순서를 기록"
                         "(엑셀 메뉴·달력처럼 화면만 봐선 알 수 없는 순서를 잡는다)")
    ap.add_argument("--record-seconds", type=float, default=300.0,
                    help="--record 최대 기록 시간(초, 기본 300)")
    ap.add_argument("--watch", action="store_true",
                    help="--inspect 시 로그인만 하면, 화면 전환을 지켜보다가 "
                         "신용카드(회계 전표) 화면이 뜨는 순간 자동으로 잡는다")
    ap.add_argument("--watch-seconds", type=float, default=300.0,
                    help="--watch 최대 감시 시간(초, 기본 300)")
    ap.add_argument("--no-keep-open", action="store_true",
                    help="작업이 끝나면 묻지 않고 브라우저를 닫는다(무인 실행용)")
    args = ap.parse_args(argv)

    from kafa.fetch.plan import build_plan, months_between, recent_months
    from kafa.fetch.wehago import (NotCalibrated, load_fetch_config,
                                   missing_selectors, run_fetch)

    cfg = load_fetch_config(args.config)

    if args.inspect:
        from kafa.fetch.inspect import inspect_page, screen_hint, watch_screens
        from kafa.fetch.session import browser_page, wait_for_human
        print(TOS_NOTICE)
        with browser_page(profile_dir=args.profile,
                          attach_port=args.attach_port) as page:
            if args.goto:
                wait_for_human("브라우저에서 **로그인만** 해 주세요. "
                               "엔터를 누르면 신용카드 화면으로 이동합니다.")
                page.goto(args.goto, timeout=int(cfg.get("timeout_ms", 20000)))
                wait_for_human("화면이 다 뜨면 엔터를 눌러 주세요(표·버튼이 보일 때까지 기다렸다가).")
            elif args.record:
                wait_for_human(
                    "브라우저에서 **로그인하고 신용카드(매입) 화면까지** 가 주세요.\n"
                    "엔터를 누른 뒤, 평소처럼 기간을 고르고 조회한 다음 엑셀을 한 번\n"
                    "받아 보시면 그 순서를 기록합니다.")
            elif args.watch:
                wait_for_human("브라우저에서 **로그인만** 해 주세요.\n"
                               "엔터를 누른 뒤에는 평소처럼 수임처 › 회계 › 신용카드(매입) 로 "
                               "이동만 하시면, 화면을 자동으로 잡습니다.")
            else:
                wait_for_human("브라우저에서 로그인하고, 보정할 화면을 열어 두세요.\n"
                               + str(cfg.get("start_hint", "")))
            # 붙여넣기/전달이 쉽도록 파일로도 남긴다(화면 구조만 — 입력값·거래처명 없음).
            default_out = "kafa-record.txt" if args.record else "kafa-inspect.txt"
            out = Path(args.inspect_out or default_out)
            while True:
                if args.record:
                    from kafa.fetch.record import record_flow
                    lines = record_flow(page, seconds=args.record_seconds,
                                        on_event=print)
                elif args.watch:
                    lines = watch_screens(page, seconds=args.watch_seconds,
                                          on_event=print)
                else:
                    lines = inspect_page(page)
                for line in lines:
                    print(line)
                out.write_text("\n".join(lines), encoding="utf-8")
                print(f"\n[저장] {out.resolve()}"
                      "  ← 이 파일을 보내주시면 selector 를 채워 드립니다.")
                # 판정을 맨 마지막에 한 번 더 — 출력이 길어 위로 밀려 안 보이기 쉽다.
                if not args.record:
                    print()
                    for line in screen_hint(lines):
                        print(line)
                if args.no_keep_open:
                    break
                # 화면을 잘못 잡았을 때 브라우저를 다시 띄우지 않고 그 자리에서 재시도한다.
                ans = input_fn("\n브라우저는 열어 둔 채입니다. 화면을 옮긴 뒤 "
                               "다시 살펴보려면 r + 엔터, 끝내려면 그냥 엔터: ")
                if (ans or "").strip().lower() not in ("r", "ㄱ", "다시"):
                    break
        return 0

    urls: dict = {}
    if args.master:
        from kafa.clients import client_urls_from_excel
        urls = client_urls_from_excel(args.master)
        print(f"수임처 마스터: URL {len(urls)}곳 (주소로 바로 이동 — 화면 검색 생략)")

    if not args.inbox or not (args.clients or urls):
        ap.error("--inbox 와 --clients(또는 --master) 가 필요합니다(또는 --inspect).")

    clients = _clients_from(args.clients) if args.clients else list(urls)
    if args.frm and args.to:
        periods = months_between(args.frm, args.to)
    elif args.months:
        periods = recent_months(args.months)
    else:
        ap.error("--months 또는 --from/--to 로 기간을 지정하세요.")

    plan = build_plan(args.inbox, clients, periods, archive=args.archive, urls=urls)
    print(f"거래처 {len(clients)}곳 × 기간 {len(periods)}개월 = {plan.total}건")
    print(f"  받을 것 {len(plan.tasks)}건 / 이미 있음 {len(plan.skipped)}건")

    if args.dry_run:
        for t in plan.tasks[:40]:
            print(f"   - {t.client}/{t.period}")
        if len(plan.tasks) > 40:
            print(f"   … 외 {len(plan.tasks) - 40}건")
        return 0

    url_mode = bool(plan.tasks) and all(t.url for t in plan.tasks)
    miss = missing_selectors(cfg, url_mode=url_mode)
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
            res = run_fetch(
                page, plan, args.inbox, cfg=cfg,
                on_progress=lambda t, s: print(f"  [{s}] {t.client}/{t.period}"),
                on_session_expired=lambda: wait_for_human(
                    "로그인이 풀렸습니다. 브라우저에서 다시 로그인해 주세요."))
        except NotCalibrated as e:
            print(f"\n[중단] {e}", file=sys.stderr)
            return 2
        if not args.no_keep_open:
            input_fn("\n브라우저는 열어 둔 채입니다. 확인이 끝나면 엔터를 누르세요... ")

    print(f"\n저장 {len(res.saved)}건 / 실패 {len(res.failures)}건 / 생략 {res.skipped}건")
    for k, v in res.failures.items():
        print(f"  [실패] {k} → {v}", file=sys.stderr)
    print(f"\n다음: kafa-pipeline {args.inbox} <출력폴더>")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
