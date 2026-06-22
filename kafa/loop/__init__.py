"""루프 엔지니어링(Actor↔Evaluator) — 생성→평가→재생성 반복 프레임워크.

생성자(Actor)가 결과물을 만들고, 평가자(Evaluator)가 루브릭으로 채점·비평하면,
오케스트레이터가 비평을 다시 Actor 에 주입해 통과(또는 최대 횟수)까지 반복한다.

설계 원칙(kafa 일관):
- 공급자 비종속: 모델 호출은 주입형 Completion(콜러블)로 — 테스트는 가짜, 실사용은 opt-in.
- 프롬프트/루브릭은 config/loops/*.yaml 로 외부화(코드 수정 없이 새 주제 적용).
- temperature 대신 effort(현재 Claude는 temperature 미지원). Evaluator는 결정성 위해 effort 낮게 + 구조화 JSON.
- 이력 보관: 매 반복의 결과물/점수/비평을 로그로 남김.

빠른 사용(로컬 기본 = Claude CLI, 별도 API·추가 토큰 없음):
    from kafa.loop import run_loop, load_loop_spec, default_completion
    spec = load_loop_spec("example")              # config/loops/example.yaml
    model = default_completion()                  # 로컬 claude CLI 우선 → API 폴백
    result = run_loop(spec, "입력(비-PII)", model, log_path="logs/run.jsonl")
    print(result.passed, result.best_score, result.best_output)
"""
from kafa.loop.clients import (
    AnthropicCompletion,
    CliCompletion,
    Completion,
    FunctionCompletion,
    default_completion,
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
    "Completion", "FunctionCompletion", "CliCompletion", "AnthropicCompletion",
    "default_completion", "parse_evaluation",
    "load_loop_spec", "loop_spec_from_dict", "available_loops",
    "run_loop", "EVAL_SCHEMA",
]
