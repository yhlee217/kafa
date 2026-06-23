"""Actor / Evaluator 표준 프롬프트 — LoopSpec 으로 렌더링.

사용자 제공 포맷(Part A Actor / Part B Evaluator)을 다듬은 한국어 표준 프롬프트.
실제 문구(역할/작업/루브릭)는 LoopSpec(=YAML)에서 주입되어 코드 수정 없이 바뀐다.

보안 제0원칙: 이 프롬프트에 넣는 input_data 는 호출자가 책임지고 비-PII 로 만든다
(거래처 실명·사업자번호 금지). 프레임워크는 받은 문자열을 그대로 전달만 한다.
"""
from __future__ import annotations

from kafa.loop.models import LoopSpec

_NO_CRITIQUE = "(없음 — 최초 시도)"


def render_actor_system(spec: LoopSpec) -> str:
    """Actor 시스템 프롬프트 — 역할/작업/출력형식 주입."""
    parts = [
        f"당신은 {spec.role}이다.",
        "주어진 작업을 수행해 최고 품질의 결과물을 만든다.",
        "",
        "[작업]",
        spec.task.strip(),
    ]
    if spec.output_format.strip():
        parts += ["", "[출력 형식]", spec.output_format.strip()]
    parts += [
        "",
        "[규칙]",
        "- 평가자의 비평(critique)이 주어지면 그 지적을 빠짐없이 반영해 개선한다.",
        "- 비평이 없으면(최초 시도) 작업 지시와 출력 형식에 충실하게 작성한다.",
        "- 결과물만 출력한다. 메타 설명·사과·서론은 넣지 않는다.",
    ]
    return "\n".join(parts)


def render_actor_user(spec: LoopSpec, input_data: str, critique: str | None,
                      prev_output: str | None = None) -> str:
    """Actor 사용자 프롬프트 — 입력 + 직전 결과물/비평(있으면).

    직전 결과물을 함께 주어 '처음부터 새로 쓰기'가 아니라 '비평대로 고치기'를 유도한다
    (회차 간 품질 퇴행 방지 — 잘된 부분은 유지하고 지적된 부분만 수정).
    """
    blocks = ["[입력]", input_data]
    if prev_output and prev_output.strip():
        blocks += ["", "[직전 결과물 — 아래 비평대로 이 결과물을 고쳐라(잘된 부분은 유지)]",
                   prev_output.strip()]
    blocks += ["", "[이전 비평]",
               critique.strip() if critique and critique.strip() else _NO_CRITIQUE]
    return "\n".join(blocks)


def render_evaluator_system(spec: LoopSpec) -> str:
    """Evaluator 시스템 프롬프트 — 루브릭/통과기준 주입, JSON 강제."""
    rubric = "\n".join(f"- {r}" for r in spec.rubric) or "- (루브릭 미정 — 일반 품질 기준)"
    return "\n".join(
        [
            "당신은 엄격하고 공정한 평가자다.",
            "Actor 의 결과물을 아래 루브릭으로 0~100점으로 채점하고, 통과 여부와",
            "구체적 개선 비평을 낸다.",
            "",
            "[루브릭]",
            rubric,
            "",
            "[채점 원칙]",
            "- 각 기준을 독립적으로 검토한 뒤 종합 점수를 매긴다.",
            f"- 점수는 0~100 정수. 통과 기준은 {spec.pass_score}점 이상.",
            "- critique 는 '무엇을 어떻게 고칠지' 행동 가능한 지적으로 쓴다.",
            "  통과면 남은 약점을 간단히 적는다.",
            "- 반드시 지정된 JSON 스키마만 출력한다(설명 문장 금지).",
        ]
    )


def render_evaluator_user(spec: LoopSpec, input_data: str, output: str) -> str:
    """Evaluator 사용자 프롬프트 — 작업/입력/평가대상."""
    return (
        "[원래 작업]\n"
        f"{spec.task.strip()}\n\n"
        "[입력]\n"
        f"{input_data}\n\n"
        "[평가 대상 결과물]\n"
        f"{output}"
    )
