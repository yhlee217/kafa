"""로컬 Claude Code CLI 를 모델로 써서 루프를 실제로 돌리는 실행기.

별도 API 키도, 추가 토큰 과금도 없다 — 설치된 `claude` 를 구독 인증으로 부른다.
프로그램이 사람 개입 없이 생성→평가→재생성을 반복한다.

사용:
    python examples/run_loop_cli.py <loop_name> "<비-PII 입력>" [--model M] [--log run.jsonl]
예:
    python examples/run_loop_cli.py example \
        "업태=음식점업 / 종목=한식 / 품명=직원 점심 식대 / 유형=카과 / 계정=(판)복리후생비"

보안 제0원칙: 입력에 거래처 실명·사업자번호 등 PII 를 절대 넣지 말 것
(업태/종목/품명/유형 같은 비-PII 특징만).
"""
from __future__ import annotations

import argparse

from kafa.loop import CliCompletion, load_loop_spec, run_loop


def main() -> None:
    ap = argparse.ArgumentParser(description="로컬 claude CLI 로 Actor↔Evaluator 루프 실행")
    ap.add_argument("loop", help="config/loops/<loop>.yaml 의 이름 (예: example)")
    ap.add_argument("input", help="비-PII 입력 문자열")
    ap.add_argument("--model", default=None, help="CLI 모델 (미지정 시 구독 기본값)")
    ap.add_argument("--log", default=None, help="JSONL 추적 로그 경로")
    args = ap.parse_args()

    spec = load_loop_spec(args.loop)
    model = CliCompletion(model=args.model)
    res = run_loop(spec, args.input, model, log_path=args.log)

    print(f"통과={res.passed} 종료={res.stopped_reason} "
          f"최고점={res.best_score} 회차={len(res.iterations)}")
    for it in res.iterations:
        tag = "PASS" if it.is_passed else "fail"
        print(f"\n[{it.index}] score={it.score} {tag}")
        print(f"  출력: {it.output}")
        if not it.is_passed:
            print(f"  ↳ 비평: {it.critique}")
    print("\n── 최종 채택 ──")
    print(res.best_output)


if __name__ == "__main__":
    main()
