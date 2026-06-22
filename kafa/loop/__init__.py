"""루프 엔지니어링(Actor↔Evaluator) — 생성→평가→재생성 반복 프레임워크.

생성자(Actor)가 결과물을 만들고, 평가자(Evaluator)가 루브릭으로 채점·비평하면,
오케스트레이터가 비평을 다시 Actor 에 주입해 통과(또는 최대 횟수)까지 반복한다.

설계 원칙(kafa 일관):
- 공급자 비종속: 모델 호출은 주입형 Completion(콜러블)로 — 테스트는 가짜, 실사용은 opt-in.
- 프롬프트/루브릭은 config/loops/*.yaml 로 외부화(코드 수정 없이 새 주제 적용).
- temperature 대신 effort(현재 Claude는 temperature 미지원). Evaluator는 결정성 위해 effort 낮게 + 구조화 JSON.
- 이력 보관: 매 반복의 결과물/점수/비평을 로그로 남김.

빠른 사용(테스트/실사용 공통):
    from kafa.loop import run_loop, load_loop_spec, AnthropicCompletion
    spec = load_loop_spec("example")              # config/loops/example.yaml
    actor = AnthropicCompletion()                 # opt-in(추가 토큰). 테스트는 가짜 콜러블.
    result = run_loop(spec, "입력(비-PII)", actor, log_path="logs/run.jsonl")
    print(result.passed, result.best_score, result.best_output)
"""
from kafa.loop.clients import (
    AnthropicCompletion,
    Completion,
    FunctionCompletion,
    parse_evaluation,
)
from kafa.loop.config_loader import (
    available_loops,
    load_loop_spec,
    loop_spec_from_dict,
)
from kafa.loop.models import Iteration, LoopResult, LoopSpec
from kafa.loop.orchestrator import EVAL_SCHEMA, run_loop

__all__ = [
    "LoopSpec", "Iteration", "LoopResult",
    "Completion", "FunctionCompletion", "AnthropicCompletion", "parse_evaluation",
    "load_loop_spec", "loop_spec_from_dict", "available_loops",
    "run_loop", "EVAL_SCHEMA",
]
