"""루프 엔지니어링(Actor↔Evaluator) 단위테스트 — 가짜 콜러블로 제어흐름 검증.

실제 모델 호출 없이: 통과-즉시 / 비평-반영-개선 / 최대반복-폴백 / 추적성로그 /
평가 JSON 파싱 견고성 / Anthropic 어댑터(temperature 미사용·effort 전달)를 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kafa.loop import (
    AnthropicCompletion,
    LoopSpec,
    available_loops,
    load_loop_spec,
    parse_evaluation,
    run_loop,
)


def _spec(**kw) -> LoopSpec:
    base = dict(name="t", role="도우미", task="작업", output_format="형식",
                rubric=["기준1", "기준2"], max_iter=4, pass_score=90)
    base.update(kw)
    return LoopSpec(**base)


def _evaluator(score: int, passed: bool, critique: str = ""):
    def fn(system, user, *, effort=None, json_schema=None, model=None):
        return json.dumps({"score": score, "is_passed": passed,
                           "critique": critique}, ensure_ascii=False)
    return fn


# ── 제어흐름 ──

def test_passes_on_first_iteration():
    actor = lambda s, u, **k: "결과물"
    res = run_loop(_spec(), "입력", actor, _evaluator(95, True, "좋음"))
    assert res.passed and res.stopped_reason == "passed"
    assert len(res.iterations) == 1
    assert res.best_output == "결과물" and res.best_score == 95


def test_passes_by_score_even_if_not_flagged():
    # is_passed=False 라도 score>=pass_score 면 통과로 종료.
    actor = lambda s, u, **k: "결과물"
    res = run_loop(_spec(pass_score=90), "입력", actor, _evaluator(92, False))
    assert res.passed and res.stopped_reason == "passed"
    assert len(res.iterations) == 1


def test_improves_with_injected_critique():
    seen_user = []

    def actor(system, user, *, effort=None, json_schema=None, model=None):
        seen_user.append(user)
        return "개선본" if "더 구체적으로" in user else "초안"

    def evaluator(system, user, *, effort=None, json_schema=None, model=None):
        if "개선본" in user:
            return '{"score": 96, "is_passed": true, "critique": "충분"}'
        return '{"score": 50, "is_passed": false, "critique": "더 구체적으로 써라"}'

    res = run_loop(_spec(), "입력", actor, evaluator)
    assert res.passed and len(res.iterations) == 2
    assert res.best_output == "개선본" and res.best_score == 96
    # 2번째 Actor 호출의 user 프롬프트에 1번째 비평이 주입됐는지
    assert "더 구체적으로 써라" in seen_user[1]
    # 최초 호출엔 비평 없음 표기
    assert "최초 시도" in seen_user[0]


def test_max_iter_fallback_returns_best():
    # 매 회차 점수가 다르고 통과 못 함 → max_iter 후 최고점본 채택.
    scores = iter([40, 80, 60, 55])

    def evaluator(system, user, *, effort=None, json_schema=None, model=None):
        s = next(scores)
        return json.dumps({"score": s, "is_passed": False, "critique": f"개선{s}"})

    n = {"i": 0}

    def actor(system, user, *, effort=None, json_schema=None, model=None):
        n["i"] += 1
        return f"안{n['i']}"

    res = run_loop(_spec(max_iter=4), "입력", actor, evaluator)
    assert not res.passed and res.stopped_reason == "max_iter"
    assert len(res.iterations) == 4
    assert res.best_score == 80 and res.best_output == "안2"


def test_self_evaluation_when_evaluator_omitted():
    # evaluator 미지정 → actor 가 자기평가(여기선 입력 분기로 구분).
    def actor(system, user, *, effort=None, json_schema=None, model=None):
        if "평가 대상 결과물" in user:  # Evaluator 프롬프트
            return '{"score": 91, "is_passed": true, "critique": "ok"}'
        return "산출물"  # Actor 프롬프트

    res = run_loop(_spec(), "입력", actor)  # evaluator 생략
    assert res.passed and res.best_output == "산출물"


# ── 추적성 로그 ──

def test_traceability_log_written(tmp_path: Path):
    log = tmp_path / "logs" / "run.jsonl"
    scores = iter([30, 50, 70, 88])

    def evaluator(system, user, *, effort=None, json_schema=None, model=None):
        return json.dumps({"score": next(scores), "is_passed": False,
                           "critique": "c"})

    run_loop(_spec(max_iter=4), "입력", lambda s, u, **k: "o", evaluator,
             log_path=str(log))
    lines = log.read_text("utf-8").strip().splitlines()
    assert len(lines) == 4  # 회차마다 1줄
    recs = [json.loads(ln) for ln in lines]
    assert [r["index"] for r in recs] == [0, 1, 2, 3]
    assert [r["score"] for r in recs] == [30, 50, 70, 88]
    assert all(r["loop"] == "t" and "ts" in r for r in recs)


# ── effort/모델 전달(temperature 미사용) ──

def test_actor_evaluator_receive_effort_and_model():
    captured = {}

    def actor(system, user, *, effort=None, json_schema=None, model=None):
        captured["actor"] = {"effort": effort, "model": model}
        return "o"

    def evaluator(system, user, *, effort=None, json_schema=None, model=None):
        captured["eval"] = {"effort": effort, "model": model,
                            "schema": json_schema is not None}
        return '{"score": 99, "is_passed": true, "critique": ""}'

    spec = _spec(actor_effort="high", evaluator_effort="low",
                 actor_model="m-a", evaluator_model="m-e")
    run_loop(spec, "입력", actor, evaluator)
    assert captured["actor"] == {"effort": "high", "model": "m-a"}
    assert captured["eval"]["effort"] == "low"
    assert captured["eval"]["model"] == "m-e"
    assert captured["eval"]["schema"] is True  # 평가자는 구조화 JSON 스키마 전달


# ── 평가 JSON 파싱 견고성 ──

def test_parse_evaluation_handles_fenced_and_noise():
    out = parse_evaluation("설명...\n```json\n{\"score\": 77, "
                           "\"is_passed\": false, \"critique\": \"x\"}\n```\n끝")
    assert out == {"score": 77, "is_passed": False, "critique": "x"}


def test_parse_evaluation_clamps_score():
    assert parse_evaluation('{"score": 150, "is_passed": true}')["score"] == 100
    assert parse_evaluation('{"score": -5, "is_passed": false}')["score"] == 0
    # 누락 필드 기본값
    p = parse_evaluation('{"score": 80}')
    assert p["is_passed"] is False and p["critique"] == ""


def test_parse_evaluation_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_evaluation("점수 없음")


# ── YAML 로더 ──

def test_load_example_loop_spec():
    spec = load_loop_spec("example")
    assert spec.name == "rationale" and spec.pass_score == 90
    assert spec.rubric and spec.evaluator_effort == "low"
    assert "example" in available_loops()


# ── Anthropic 어댑터: temperature 없음, output_config 구성 ──

def test_anthropic_completion_no_temperature_uses_effort():
    class FakeClient:
        def __init__(self):
            self.kwargs = None
            self.messages = self

        def create(self, **kwargs):
            self.kwargs = kwargs
            class _Blk:
                type = "text"
                text = '{"score": 90, "is_passed": true, "critique": ""}'
            return type("R", (), {"content": [_Blk()]})()

    fake = FakeClient()
    comp = AnthropicCompletion(client=fake, model="claude-opus-4-8")
    text = comp("sys", "usr", effort="low", json_schema={"type": "object"})
    assert '"score": 90' in text
    k = fake.kwargs
    # temperature/top_p/top_k 는 절대 전달하지 않는다(현재 Claude 400).
    assert "temperature" not in k and "top_p" not in k and "top_k" not in k
    assert k["model"] == "claude-opus-4-8"
    assert k["output_config"]["effort"] == "low"
    assert k["output_config"]["format"]["type"] == "json_schema"
