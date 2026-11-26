"""kafa-ask — 미해소 행을 Claude 에게 묻고 답을 운영에 붙인다(비-PII 질문지 왕복).

    kafa-ask prepare    받아 둔 파일에서 물을 것을 모아 질문지를 쓴다(_ai/questions.json)
    kafa-ask apply      Claude 가 쓴 답지(_ai/answers.json)를 답 모음에 합친다
    kafa-ask show       지금까지 모인 답 요약(계정별 가짓수·모름·낮은 확신)
    kafa-ask exam       처리된 이력으로 시험지를 만든다(_ai/exam_questions.json)
    kafa-ask score      Claude 시험 답(_ai/exam_answers.json)을 채점한다
    kafa-ask prior      사무실 전체 이력 추천을 켜도 되는지 잰다(한 수임처씩 빼고 맞혀 보기)

경로는 kafa-run 이 기억한 것(~/.kafa/run.json)을 쓴다. --inbox/--out 으로 바꿀 수 있다.
출력에는 수임처 이름·거래처·사업자번호가 나오지 않는다(대화에 붙여넣어도 된다).
Claude 에게 붙여넣을 프롬프트: docs/cowork_ask.md
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path


def _paths(args) -> tuple[Path, Path]:
    from kafa.run_cli import DEFAULT_INBOX, DEFAULT_OUT, load_settings
    saved = load_settings()
    inbox = Path(args.inbox or saved.get("inbox") or DEFAULT_INBOX).expanduser()
    out = Path(args.out or saved.get("out") or DEFAULT_OUT).expanduser()
    return inbox, out


def print_merge(merged) -> None:
    if merged is None:
        return
    print(merged.line())
    for qid, why in merged.rejected[:10]:
        print(f"  받지 않음 {qid[:12]} — {why}")
    if len(merged.rejected) > 10:
        print(f"  … 외 {len(merged.rejected) - 10}건")


def cmd_prepare(args) -> int:
    from kafa.pipeline.ask import prepare_questions
    inbox, out = _paths(args)
    res = prepare_questions(inbox, out, config_dir=args.config_dir)
    print_merge(res.merged)
    print(f"파일 {res.files}개 확인")
    for line in res.questions.lines():
        print(line)
    if res.failures:
        print(f"  (읽지 못한 파일 {len(res.failures)}개 — 처리 단계에서 다시 보고됩니다)")
    if res.new:
        print(f"\n질문지: {res.path}")
        print(f"Claude(Cowork)에 {res.path.parent} 폴더만 연결하고 docs/cowork_ask.md 의 "
              "프롬프트를 붙여넣으면 답지(answers.json)를 씁니다.")
    else:
        print("\n새로 물을 것이 없습니다.")
    return 0


def cmd_apply(args) -> int:
    from kafa.recommend.host_picks import merge_answers
    _, out = _paths(args)
    merged = merge_answers(out, config_dir=args.config_dir)
    if merged is None:
        print(f"답지가 없습니다: {out / '_ai' / 'answers.json'}")
        return 1
    print_merge(merged)
    return 0 if merged.archived is not None else 1


def cmd_show(args) -> int:
    from kafa.recommend.host_picks import load_picks, settings
    _, out = _paths(args)
    picks = load_picks(out)
    if not picks:
        print("아직 모인 답이 없습니다.")
        return 0
    s = settings(args.config_dir)
    by_name = Counter(p.get("name") or "모름" for p in picks.values())
    low = sum(1 for p in picks.values()
              if p.get("code") is not None and float(p.get("confidence") or 0)
              < s["min_confidence"])
    print(f"모인 답 {len(picks)}가지 · 모름 {by_name.pop('모름', 0)} · "
          f"확신 {s['min_confidence']} 미만(적용 안 함) {low}")
    for name, n in by_name.most_common(30):
        print(f"  {n:>4}  {name}")
    return 0


def cmd_exam(args) -> int:
    from kafa.recommend.host_picks import (
        EXAM_ANSWERS,
        ai_dir,
        build_exam,
        exam_key_path,
    )
    from kafa.store.db import VoucherStore
    _, out = _paths(args)
    db_path = out / "kafa.db"
    if not db_path.exists():
        print(f"처리 이력(DB)이 없습니다: {db_path}")
        return 1
    db = VoucherStore(db_path)
    try:
        records = db.account_records(config_dir=args.config_dir, father_only=args.father_only)
        usage = db.account_usage()
    finally:
        db.close()
    n = build_exam(records, out, n=args.n, usage=usage, config_dir=args.config_dir)
    old = ai_dir(out) / EXAM_ANSWERS
    if old.exists():
        old.unlink()                     # 지난 시험 답이 새 시험지에 섞이지 않게
    kind = ("담당자가 정한 행(답지·전표확정)" if args.father_only
            else "처리된 행(대부분 위하고 자동 분류 — 담당자 정답은 --father-only)")
    print(f"{kind} {len(records)}건에서 시험 문제 {n}가지를 만들었습니다.")
    print(f"시험지: {ai_dir(out) / 'exam_questions.json'}")
    print(f"정답:   {exam_key_path(out)} (_ai 밖 — Claude 에게 _ai 폴더만 연결하면 안 보인다)")
    print("Claude 에게 docs/cowork_ask.md 의 '시험' 프롬프트를 붙여넣고, 끝나면 kafa-ask score")
    return 0


def cmd_score(args) -> int:
    from kafa.recommend.host_picks import EXAM_REPORT, ai_dir, score_exam
    _, out = _paths(args)
    sc = score_exam(out, config_dir=args.config_dir)
    if sc is None:
        print("시험지나 답(exam_answers.json)이 없습니다. kafa-ask exam 부터.")
        return 1
    print(sc.render(), end="")
    print(f"\n(저장: {ai_dir(out) / EXAM_REPORT})")
    return 0


def cmd_prior(args) -> int:
    from kafa.recommend.office_prior import check_prior, settings
    from kafa.store.db import VoucherStore
    _, out = _paths(args)
    db_path = out / "kafa.db"
    if not db_path.exists():
        print(f"처리 이력(DB)이 없습니다: {db_path}")
        return 1
    db = VoucherStore(db_path)
    try:
        records = db.prior_records()
        unresolved = db.unresolved_records()
    finally:
        db.close()
    pc = check_prior(records, unresolved, config_dir=args.config_dir)
    s = settings(args.config_dir)
    print(pc.render(s), end="")
    print(f"(지금 설정: {'켜짐' if s['enabled'] else '꺼짐'} · 수임처 {s['min_clients']}곳 · "
          f"비중 {s['min_share']:.0%})")
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--inbox", help="받아 둔 다운로드본 폴더(기본: kafa-run 설정)")
    common.add_argument("--out", help="결과 폴더(기본: kafa-run 설정)")
    common.add_argument("--config-dir", default=None)
    ap = argparse.ArgumentParser(prog="kafa-ask",
                                 description="미해소 행을 Claude 에게 묻고 답을 운영에 붙인다")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", parents=[common], help="질문지 쓰기")
    sub.add_parser("apply", parents=[common], help="답지 합치기")
    sub.add_parser("show", parents=[common], help="모인 답 요약")
    ex = sub.add_parser("exam", parents=[common], help="처리 이력으로 시험지 만들기")
    ex.add_argument("--n", type=int, default=150, help="문제 수(기본 150)")
    ex.add_argument("--father-only", action="store_true",
                    help="담당자가 정한 행(답지·전표확정)만 정답으로 — 그 밖은 위하고 자동 분류다")
    sub.add_parser("score", parents=[common], help="시험 채점")
    sub.add_parser("prior", parents=[common],
                   help="사무실 전체 이력 추천 정답률(한 수임처씩 빼고 맞혀 보기)")
    args = ap.parse_args(argv)
    return {"prepare": cmd_prepare, "apply": cmd_apply, "show": cmd_show,
            "exam": cmd_exam, "score": cmd_score, "prior": cmd_prior}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
