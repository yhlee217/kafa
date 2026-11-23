"""kafa-run — 한 명령으로 수집부터 산출물까지.

    kafa-run --master 수임처마스터.xlsx     # 첫 실행(경로를 기억한다)
    kafa-run                                # 그다음부터는 이것만

사람이 하는 일은 **로그인 한 번**뿐이다. 나머지(수임처 순회·다운로드·수임처 속성 반영·
분류·업로드본 생성)는 순서대로 알아서 돈다. 중간에 끊겨도 다시 실행하면 이어서 간다.

설정은 `~/.kafa/run.json` 에 남는다(경로만 — 원천 데이터는 저장하지 않는다).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SETTINGS = Path.home() / ".kafa" / "run.json"
DEFAULT_INBOX = Path.home() / "kafa-inbox"
DEFAULT_OUT = Path.home() / "kafa-out"


def load_settings(path=None) -> dict:
    try:
        return json.loads(Path(path or SETTINGS).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 처음부터
        return {}


def save_settings(data: dict, path=None) -> None:
    try:
        p = Path(path or SETTINGS)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except Exception:  # noqa: BLE001 — 저장 실패가 실행을 막지 않는다
        pass


def _banner(step: int, total: int, title: str) -> None:
    print(f"\n{'=' * 60}\n[{step}/{total}] {title}\n{'=' * 60}")


ASK_WAIT = 3          # 종료코드: Claude 답을 기다리는 중(오류 아님)


def _ask_first(inbox: Path, out: Path, *, no_ask: bool = False) -> bool:
    """분류 전에 Claude 에게 물을 것을 정리한다. 새 질문이 있고 기다리기로 했으면 True.

    답지(answers.json)가 이미 있으면 먼저 합치고 나서 센다. 한 번 물은 특징(모름 포함)은
    다시 묻지 않으므로, 답을 받고 다시 실행하면 그대로 넘어간다.
    """
    from kafa.ask_cli import print_merge
    from kafa.pipeline.ask import prepare_questions
    from kafa.recommend.host_picks import settings
    s = settings()
    if not s["enabled"]:
        print("(꺼져 있음 — config recommend.host_picks.enabled)")
        return False
    try:
        res = prepare_questions(inbox, out)
    except Exception as e:  # noqa: BLE001 — 질문 정리가 실패해도 분류는 막지 않는다
        print(f"질문 정리를 건너뜁니다: {type(e).__name__}")
        return False
    print_merge(res.merged)
    for line in res.questions.lines():
        print(line)
    if not res.new:
        print("새로 물을 것이 없습니다 — 바로 분류합니다.")
        return False
    if no_ask or not s["ask_before_run"]:
        print("묻지 않고 진행합니다(이번 질문은 다음에 다시 나옵니다).")
        return False
    print(f"""
Claude 에게 물을 것이 {res.new}가지 있어 여기서 멈춥니다.
  질문지: {res.path}
          (업태·종목·품명·유형과 건수만 — 거래처·사업자번호·금액 없음)
  1) Claude(Cowork)에 {res.path.parent} 폴더만 연결하고 docs/cowork_ask.md 의
     프롬프트를 붙여넣으면 답지(answers.json)를 씁니다.
  2) 다 되면 다시:  kafa-run --skip-fetch
  묻지 않고 그냥 진행:  kafa-run --skip-fetch --no-ask""")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kafa-run",
        description="수집 → 수임처 속성 반영 → 분류·업로드본 생성을 한 번에")
    ap.add_argument("--master", help="수임처 마스터 엑셀(첫 실행에만 필요 — 기억한다)")
    ap.add_argument("--inbox", help=f"받을 폴더(기본 {DEFAULT_INBOX})")
    ap.add_argument("--out", help=f"결과 폴더(기본 {DEFAULT_OUT})")
    ap.add_argument("--clients", help="일부만 돌리기: 쉼표로 구분한 수임처 이름")
    ap.add_argument("--profile", help="브라우저 프로필 폴더(로그인 유지)")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="이미 받아둔 파일만 처리(브라우저 안 띄움)")
    ap.add_argument("--dry-run", action="store_true", help="무엇을 받을지만 보여준다")
    ap.add_argument("--log", nargs="?", const="kafa-run.log", metavar="파일",
                    help="진행 로그를 파일로 남긴다(이름은 번호로 가려짐)")
    ap.add_argument("--shots", nargs="?", const="kafa-shots", metavar="폴더",
                    help="수임처마다 화면 사진을 남긴다(거래내역·거래처명 흐림 처리)")
    ap.add_argument("--no-ask", action="store_true",
                    help="Claude 에게 물을 것이 있어도 기다리지 않고 바로 분류한다")
    ap.add_argument("--probe", action="store_true",
                    help="받지 않고 전부 한 바퀴 돌며 점검만 한다"
                         "(자료 있는 곳·막히는 곳을 표로)")
    args = ap.parse_args(argv)

    saved = load_settings()
    master = args.master or saved.get("master")
    inbox = Path(args.inbox or saved.get("inbox") or DEFAULT_INBOX).expanduser()
    out = Path(args.out or saved.get("out") or DEFAULT_OUT).expanduser()
    profile = args.profile or saved.get("profile")

    if not master:
        ap.error("처음에는 --master 로 수임처 마스터 엑셀 경로를 알려주세요.\n"
                 "       (한 번만 하면 기억합니다)")
    if not Path(master).expanduser().exists():
        ap.error(f"수임처 마스터 파일이 없습니다: {master}")
    save_settings({"master": str(Path(master).expanduser()), "inbox": str(inbox),
                   "out": str(out), **({"profile": profile} if profile else {})})

    total = 3 if args.skip_fetch else 4
    step = 0

    if not args.skip_fetch:
        step += 1
        _banner(step, total, "수집 — 브라우저가 열리면 로그인만 해 주세요")
        from kafa.fetch.cli import main as fetch_main
        argv_fetch = ["--inbox", str(inbox), "--master", str(master), "--whole",
                      "--no-keep-open"]
        if args.clients:
            argv_fetch += ["--clients", args.clients]
        if profile:
            argv_fetch += ["--profile", profile]
        if args.dry_run:
            argv_fetch.append("--dry-run")
        if args.probe:
            argv_fetch.append("--probe")
        if args.shots:
            argv_fetch += ["--shots", args.shots]
        if args.log:
            argv_fetch += ["--log", args.log]
        rc = fetch_main(argv_fetch)
        if args.dry_run or args.probe:
            return rc
        if rc not in (0, 1):        # 1 = 일부 실패(계속 진행), 2 이상은 중단
            return rc

    step += 1
    _banner(step, total, "수임처 속성(개인/법인) 반영")
    from kafa.clients_cli import main as clients_main
    clients_main(["from-master", str(master)])

    step += 1
    _banner(step, total, "Claude 에게 물을 것 정리(업태·종목·품명·유형만)")
    if _ask_first(inbox, out, no_ask=args.no_ask):
        return ASK_WAIT

    step += 1
    _banner(step, total, "분류 · 업로드본 생성")
    from kafa.pipeline.cli import main as pipeline_main
    rc = pipeline_main([str(inbox), str(out)])

    print(f"\n{'=' * 60}")
    print(f"끝났습니다. 결과 폴더: {out}")
    print("  업로드본 <파일>_upload.xls / 검토 리포트 _review.txt / 대조용 _review.csv")
    print("  다시 실행하면 아직 안 받은 수임처만 이어서 받습니다.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
