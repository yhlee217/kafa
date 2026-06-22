"""루프 엔지니어링 데이터 모델 — 스펙/반복기록/결과.

LoopSpec 은 config/loops/*.yaml 로 외부화되는 '한 주제의 루프 설정'이다.
코드 수정 없이 새 주제(역할/작업/루브릭/통과기준)를 추가할 수 있게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class LoopSpec:
    """한 루프 주제의 설정. 프롬프트·루브릭·종료조건을 외부화한다.

    actor_effort / evaluator_effort: temperature 대신 effort 로 강도 제어
    (현재 Claude(Opus 4.8/Fable 5)는 temperature 미지원). 평가자는 결정성을
    위해 effort 를 낮게(기본 'low') 두고 구조화 JSON 으로 받는다.
    """

    name: str
    role: str  # Actor 가 맡을 역할/페르소나
    task: str  # 수행할 작업 설명
    output_format: str = ""  # 기대 출력 형식(자유 텍스트)
    rubric: list[str] = field(default_factory=list)  # 평가 기준 목록
    max_iter: int = 4  # 최대 반복(폴백 전까지)
    pass_score: int = 90  # 통과 점수(0~100)
    actor_effort: Optional[str] = None  # None=모델 기본. 'low'|'medium'|'high'|'xhigh'|'max'
    evaluator_effort: Optional[str] = "low"  # 평가자는 낮게(결정성)
    actor_model: Optional[str] = None  # None=Completion 기본 모델
    evaluator_model: Optional[str] = None


@dataclass(frozen=True)
class Iteration:
    """한 번의 생성→평가 기록(추적성)."""

    index: int  # 0-기반 반복 회차
    output: str  # Actor 결과물
    score: int  # Evaluator 점수(0~100)
    is_passed: bool  # Evaluator 통과 판정
    critique: str  # 다음 회차에 주입할 개선 비평


@dataclass(frozen=True)
class LoopResult:
    """루프 종료 결과. 폴백 시에도 '가장 높은 점수' 결과물을 돌려준다."""

    passed: bool  # 통과로 종료했는가
    best_output: str  # 최종 채택 결과물(통과본 또는 최고점본)
    best_score: int  # best_output 의 점수
    iterations: list[Iteration]  # 전 회차 기록
    stopped_reason: str  # "passed" | "max_iter"
