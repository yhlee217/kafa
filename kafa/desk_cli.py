"""kafa-desk — 아버님 PC 에서 더블클릭으로 쓰는 명령(처리하기.bat / 업로드본_만들기.bat 가 부른다).

    kafa-desk setup   [--base 폴더] [--import-db kafa.db] [--import-clients clients.yaml]
    kafa-desk prepare [--base 폴더]   처리하기 — 답지·클로드 질문 파일 만들기(클로드 답이 있으면 먼저 반영)
    kafa-desk finish  [--base 폴더]   업로드본 만들기 — 답지의 계정으로 업로드본, 답은 이력에 쌓기

쓰는 법: docs/desk.md
"""
from __future__ import annotations

import argparse
import sys


def _prepare(args) -> int:
    from kafa.desk import CLAUDE, PROMPT_FILE, QUESTION_FILE, prepare
    rep = prepare(args.base, config_dir=args.config_dir)
    for line in rep.claude_notes:
        print(f"[클로드 답 반영] {line}")
    print(f"받은 자료 {rep.files}개를 읽었습니다.")
    for name, why in rep.failures.items():
        print(f"  [읽지 못함] {name} → {why}")
    print(f"\n답지: {rep.sheet}")
    print(f"  확인할 건 {rep.rows}건 · 그중 계정이 비어 있는 건 {rep.unresolved}건"
          + (f" · 클로드 답으로 채운 건 {rep.ai_filled}건" if rep.ai_filled else "")
          + (f" · 이미 고치신 칸 {rep.kept}건은 그대로 두었습니다" if rep.kept else ""))
    if rep.questions:
        print(f"\n클로드에게 물을 것이 {rep.questions}가지 있습니다.")
        print(f"  {CLAUDE} 폴더의 '{QUESTION_FILE}' 을 클로드에 올리고 '{PROMPT_FILE}' 의 말을 붙여넣으세요.")
        print(f"  클로드가 준 답을 {CLAUDE} 폴더에 넣고 '처리하기'를 한 번 더 누르면 답지에 채워집니다.")
        print("  (클로드 없이 바로 답지를 채우셔도 됩니다)")
    print("\n답지를 확인·수정하고 저장한 뒤 '업로드본_만들기'를 누르세요.")
    return 1 if rep.failures else 0


def _finish(args) -> int:
    from kafa.desk import UPLOADS, finish
    rep = finish(args.base, config_dir=args.config_dir)
    res = rep.result
    print(f"답지에서 계정 {rep.answered}건, 올리지 않음 {rep.excluded}건을 반영했습니다.")
    for o in res.outcomes:
        print(f"  [{o.client}/{o.period}] 업로드 {o.written}건 · 스킵 {o.skipped}건 · {o.integrity}")
    for name, why in res.failures.items():
        print(f"  [실패] {name} → {why}")
    if rep.copied:
        print(f"\n업로드본: {UPLOADS} 폴더 ({sum(1 for p in rep.copied if p.suffix.lower() == '.xls')}개)")
        print("  위하고 '신용카드 매입 엑셀 업로드'에서 올리세요. 대사가 안 맞은 파일은 만들지 않습니다.")
    if rep.archived:
        print(f"답지는 {rep.archived.parent.name} 폴더로 옮겼습니다. 고른 계정은 다음 달부터 자동으로 쓰입니다.")
    elif res.failures:
        print("\n실패한 파일은 받은자료 폴더에 남아 있습니다. 고친 뒤 '처리하기'부터 다시 해 주세요.")
    print("\n(이 화면에는 수임처 이름이 있으니 클로드에 붙여넣지 마세요)")
    return 0 if res.ok else 1


def _setup(args) -> int:
    from kafa.desk import setup
    for line in setup(args.base, import_db=args.import_db, import_clients=args.import_clients,
                      force=args.force):
        print(line)
    print("\n이제 '1_받은자료/<수임처>' 폴더에 위하고 신용카드 파일을 넣고 '처리하기.bat'을 누르세요.")
    return 0


def main(argv: list[str] | None = None) -> int:
    from kafa.desk import DeskError
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", default=None, help="kafa 폴더(기본: 홈 폴더의 kafa)")
    common.add_argument("--config-dir", default=None)
    ap = argparse.ArgumentParser(prog="kafa-desk", description="아버님 PC 용 — 처리하기 / 업로드본 만들기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", parents=[common], help="폴더·실행 아이콘 만들기, 이력 옮기기")
    s.add_argument("--import-db", help="아드님 PC 의 처리 이력(kafa-out/kafa.db)")
    s.add_argument("--import-clients", help="아드님 PC 의 수임처 속성(~/.kafa/clients.yaml)")
    s.add_argument("--force", action="store_true", help="이미 있어도 덮어쓴다")
    sub.add_parser("prepare", parents=[common], help="처리하기")
    sub.add_parser("finish", parents=[common], help="업로드본 만들기")
    args = ap.parse_args(argv)
    try:
        return {"setup": _setup, "prepare": _prepare, "finish": _finish}[args.cmd](args)
    except DeskError as e:
        print(f"[멈춤] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
