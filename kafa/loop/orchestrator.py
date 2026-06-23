"""오케스트레이터 — Init → While → Actor → Evaluator → 분기/폴백.

제어 흐름(사용자 Section 3 을 다듬음):
  critique = None                       # 최초엔 비평 없음
  for i in range(max_iter):
      output  = Actor(input, critique)
      verdict = Evaluator(input, output) # JSON {score, is_passed, critique}
      기록(추적성 로그)
      if is_passed or score >= pass_score:  # 통과 → 종료
          return 통과본
      critique = verdict.critique           # 비평 주입 후 재생성
  return 최고점본 + 경고                     # 폴백(max_iter 도달)

평가자는 결정성을 위해 effort 를 낮게(spec.evaluator_effort) 두고 구조화 JSON 으로 받는다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kafa.loop.clients import Completion, parse_evaluation
from kafa.loop.models import Iteration, LoopResult, LoopSpec
from kafa.loop import prompts

logger = logging.getLogger("kafa.loop")

# 평가자 구조화 출력 스키마(JSON). 숫자 min/max 는 스키마에 넣지 않고 로컬에서 클램프.
EVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "is_passed": {"type": "boolean"},
        "critique": {"type": "string"},
    },
    "required": ["score", "is_passed", "critique"],
    "additionalProperties": False,
}


def run_loop(
    spec: LoopSpec,
    input_data: str,
    actor: Completion,
    evaluator: Optional[Completion] = None,
    *,
    log_path: Optional[str] = None,
) -> LoopResult:
    """Actor↔Evaluator 루프를 돌려 통과본(또는 최고점본)을 돌려준다.

    actor/evaluator: Completion 콜러블. evaluator 미지정 시 actor 를 재사용한다
    (자기평가). log_path 지정 시 매 회차를 JSONL 로 누적 기록(추적성).
    """
    evaluator = evaluator or actor
    actor_sys = prompts.render_actor_system(spec)
    eval_sys = prompts.render_evaluator_system(spec)

    log_file = Path(log_path) if log_path else None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    critique: str | None = None  # 최초 시도엔 비평 없음
    prev_output: str | None = None  # 직전 결과물(고쳐쓰기용)
    iterations: list[Iteration] = []
    stopped_reason = "max_iter"

    for i in range(spec.max_iter):
        actor_user = prompts.render_actor_user(spec, input_data, critique, prev_output)
        output = actor(actor_sys, actor_user, effort=spec.actor_effort,
                       model=spec.actor_model)

        eval_user = prompts.render_evaluator_user(spec, input_data, output)
        eval_text = evaluator(eval_sys, eval_user, effort=spec.evaluator_effort,
                              json_schema=EVAL_SCHEMA, model=spec.evaluator_model)
        verdict = parse_evaluation(eval_text)

        it = Iteration(index=i, output=output, score=verdict["score"],
                       is_passed=verdict["is_passed"], critique=verdict["critique"])
        iterations.append(it)
        _log(log_file, spec, it)

        if it.is_passed or it.score >= spec.pass_score:
            stopped_reason = "passed"
            break

        critique = it.critique  # 비평 주입
        prev_output = output    # 직전 결과물도 함께 주어 '고쳐쓰기' 유도(퇴행 방지)

    if not iterations:
        # max_iter <= 0 등으로 루프 본문이 한 번도 안 돌면 빈 결과를 안전 반환.
        return LoopResult(passed=False, best_output="", best_score=0,
                          iterations=[], stopped_reason="no_iterations")

    if stopped_reason == "passed":
        best = iterations[-1]  # 통과를 일으킨 회차
    else:
        best = max(iterations, key=lambda x: x.score)  # 폴백: 최고점본
        logger.warning(
            "루프 '%s' 미통과: %d회 반복 후 최고점본 채택(점수=%d, 통과기준=%d).",
            spec.name, len(iterations), best.score, spec.pass_score)

    return LoopResult(
        passed=(stopped_reason == "passed"),
        best_output=best.output,
        best_score=best.score,
        iterations=iterations,
        stopped_reason=stopped_reason,
    )


def _log(log_file: Path | None, spec: LoopSpec, it: Iteration) -> None:
    if not log_file:
        return
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "loop": spec.name,
        "index": it.index,
        "score": it.score,
        "is_passed": it.is_passed,
        "critique": it.critique,
        "output": it.output,
    }
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
