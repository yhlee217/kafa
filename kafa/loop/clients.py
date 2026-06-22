"""모델 호출 어댑터 — 공급자 비종속(주입형) Completion.

- Completion: 콜러블 프로토콜 `(system, user, *, effort, json_schema, model) -> str`.
- AnthropicCompletion: 실사용(opt-in). client 주입 가능, anthropic SDK 지연 import.
  temperature/top_p/top_k 미사용(현재 Claude 400). 강도는 output_config.effort,
  구조화 출력은 output_config.format(json_schema)로 제어.
- FunctionCompletion: 평범한 함수를 Completion 으로 감싸는 테스트/대체용 어댑터.
- parse_evaluation: 평가자 JSON(마크다운 펜스/잡음 허용) 파싱 + 점수 클램프.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional, Protocol


class Completion(Protocol):
    """모델 1회 호출. system/user 를 받아 텍스트를 반환한다."""

    def __call__(
        self,
        system: str,
        user: str,
        *,
        effort: Optional[str] = None,
        json_schema: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> str: ...


class FunctionCompletion:
    """일반 함수를 Completion 으로 감싼다(테스트·간이 연결용)."""

    def __init__(self, fn: Callable[..., str]):
        self._fn = fn

    def __call__(self, system: str, user: str, *, effort=None, json_schema=None,
                 model=None) -> str:
        return self._fn(system, user, effort=effort, json_schema=json_schema,
                        model=model)


class AnthropicCompletion:
    """Claude API 어댑터(opt-in). 추가 토큰/키가 필요하므로 명시적으로만 사용."""

    def __init__(self, *, client=None, model: str = "claude-opus-4-8",
                 max_tokens: int = 4096):
        self._client = client
        self.model = model
        self.max_tokens = max_tokens

    def _get_client(self):
        if self._client is None:
            import anthropic  # 지연 import: SDK 는 실사용 시에만 필요
            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, system: str, user: str, *, effort=None, json_schema=None,
                 model=None) -> str:
        kwargs = dict(
            model=model or self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        out_cfg: dict = {}
        if json_schema is not None:
            out_cfg["format"] = {"type": "json_schema", "schema": json_schema}
        if effort:
            out_cfg["effort"] = effort
        if out_cfg:
            kwargs["output_config"] = out_cfg
        resp = self._get_client().messages.create(**kwargs)
        return next((b.text for b in resp.content
                     if getattr(b, "type", None) == "text"), "")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_evaluation(text: str) -> dict:
    """평가자 응답 → {score:int(0~100), is_passed:bool, critique:str}.

    마크다운 펜스/앞뒤 잡음이 섞여도 첫 JSON 객체를 추출해 파싱한다.
    """
    raw = (text or "").strip()
    m = _FENCE.search(raw)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1 or e < s:
            raise ValueError(f"평가 JSON 파싱 실패: {text!r}")
        data = json.loads(raw[s:e + 1])

    try:
        score = int(round(float(data.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    return {
        "score": score,
        "is_passed": bool(data.get("is_passed", False)),
        "critique": str(data.get("critique", "")).strip(),
    }
