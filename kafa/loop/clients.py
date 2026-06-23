"""모델 호출 어댑터 — 공급자 비종속(주입형) Completion.

- Completion: 콜러블 프로토콜 `(system, user, *, effort, json_schema, model) -> str`.
- CliCompletion: **로컬 기본**. 설치된 Claude Code CLI(`claude -p`)를 구독 인증으로 호출 →
  별도 API 키도, 추가 토큰 과금도 없음. 루프가 사람 개입 없이 스스로 돈다.
- AnthropicCompletion: API 폴백(opt-in). client 주입 가능, anthropic SDK 지연 import.
  temperature/top_p/top_k 미사용(현재 Claude 400). 강도는 output_config.effort,
  구조화 출력은 output_config.format(json_schema)로 제어.
- FunctionCompletion: 평범한 함수를 Completion 으로 감싸는 테스트/대체용 어댑터.
- default_completion: 로컬 claude CLI 우선 → 없으면 API 키 → 둘 다 없으면 에러.
- parse_evaluation: 평가자 JSON(마크다운 펜스/잡음 허용) 파싱 + 점수 클램프.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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


class CliCompletion:
    """로컬 Claude Code CLI(`claude -p`)를 모델로 사용 — 로컬 기본 백엔드.

    구독 인증으로 동작하므로 별도 API 키도, 추가 토큰 과금도 없다(프로젝트 원칙).
    `claude` 바이너리가 PATH 에 있어야 한다(Claude Code 설치 시 제공).

    호출: `claude -p <user> --system-prompt <system> [--model M] --output-format json`.
    --output-format json 의 봉투에서 result(모델 텍스트)만 꺼낸다. 구조화 출력은 CLI 에
    effort/스키마 강제가 없으므로 시스템 프롬프트에 JSON 스키마 지시를 덧붙이고
    parse_evaluation 이 견고하게 파싱한다. effort 는 CLI 가 노출하지 않아 무시한다.
    """

    def __init__(self, *, claude_bin: str = "claude", model: Optional[str] = None,
                 timeout: int = 180, extra_args: Optional[list[str]] = None):
        self.claude_bin = claude_bin
        self.model = model
        self.timeout = timeout
        self.extra_args = list(extra_args or [])

    def __call__(self, system: str, user: str, *, effort=None, json_schema=None,
                 model=None) -> str:
        sys_prompt = system
        if json_schema is not None:
            sys_prompt = (
                system
                + "\n\n[출력 형식] 아래 JSON 스키마를 만족하는 JSON 객체 하나만 출력하라"
                  "(코드펜스·설명 문장 금지):\n"
                + json.dumps(json_schema, ensure_ascii=False)
            )
        cmd = [self.claude_bin, "-p", user,
               "--system-prompt", sys_prompt,
               "--output-format", "json"]
        m = model or self.model
        if m:
            cmd += ["--model", m]
        cmd += self.extra_args

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"claude CLI 타임아웃({self.timeout}s 초과)") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 실패(code={proc.returncode}): {proc.stderr.strip()[:500]}")
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"claude CLI 출력 파싱 실패: {proc.stdout[:500]!r}") from e
        if env.get("is_error"):
            raise RuntimeError(f"claude CLI 오류: {env.get('result') or env}")
        return env.get("result", "")


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


def default_completion(*, model: Optional[str] = None, timeout: int = 180):
    """로컬 기본 백엔드 선택: claude CLI(구독·무과금) 우선 → API 키 폴백.

    둘 다 없으면 RuntimeError. 테스트/대체는 직접 콜러블을 주입하면 된다.
    """
    if shutil.which("claude"):
        return CliCompletion(model=model, timeout=timeout)
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return AnthropicCompletion(model=model or "claude-opus-4-8")
    raise RuntimeError(
        "모델 백엔드 없음: 로컬에 Claude Code CLI(`claude`) 설치 또는 "
        "ANTHROPIC_API_KEY 설정이 필요합니다.")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _coerce_bool(v) -> bool:
    """JSON 불리언/숫자/문자열을 bool 로. 문자열 'false'/'0' 은 False
    (파이썬 bool('false') == True 함정 방지)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def _first_json_object(raw: str):
    """앞뒤 잡음·여러 객체가 섞여도 첫 유효 JSON 객체(dict)를 반환. 없으면 None."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(raw[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def parse_evaluation(text: str) -> dict:
    """평가자 응답 → {score:int(0~100), is_passed:bool, critique:str}.

    마크다운 펜스/앞뒤 잡음/여러 객체가 섞여도 첫 유효 JSON 객체를 추출해 파싱한다.
    """
    raw = (text or "").strip()
    m = _FENCE.search(raw)
    if m:
        raw = m.group(1).strip()
    data = _first_json_object(raw)
    if data is None:
        raise ValueError(f"평가 JSON 파싱 실패: {text!r}")
    try:
        score = int(round(float(data.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    return {
        "score": score,
        "is_passed": _coerce_bool(data.get("is_passed", False)),
        "critique": str(data.get("critique", "")).strip(),
    }
