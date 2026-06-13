"""LLM 기반 차변계정 추정 + 근거 생성 (Claude API).

사용자 요구: "AI의 대차변 계정 추정 및 근거 생성"이 핵심. Claude 가 **비-PII 특징**
(업태/종목/품명/유형)만 보고, 허용 계정 목록 중에서 차변계정을 고르고 한국어 근거를 만든다.
거래처 실명·사업자번호는 LLM 에 전달되지 않는다(features.py 가 구조적으로 차단).

재현성: 동일 특징 서명 → 결정 캐시 재사용(같은 입력이면 같은 결과). cache_path 지정 시
런 간에도 재현. 결정 캐시에는 PII 가 없다(특징 서명 + 계정코드/근거만).

모델: 기본 claude-opus-4-8 (config 로 변경 가능). 구조화 출력(output_config.format)으로
허용 계정명 enum 을 강제하고, 로컬에서 코드로 매핑해 항상 유효한 코드만 반환한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Protocol

from kafa.config_loader import load_account_codes, load_rules
from kafa.recommend.features import feature_signature
from kafa.recommend.recommender import Recommendation

_SYSTEM = """\
당신은 한국 세무회계의 신용카드 '매입(비용)' 전표 분류 도우미다.
주어진 가맹점 업종(업태/종목)·품명·과세유형만으로, 이 매입의 **차변계정**(비용/자산 계정)을
아래 '허용 계정' 중 하나로 고른다. 거래처명·사업자번호 같은 식별정보는 제공되지 않는다.
- 제조원가는 (제), 판매관리비는 (판) 접두로 구분된다. 비용은 보통 (판) 계정이다.
- 확신이 낮으면 confidence 를 낮추고, 대안(alternatives)을 제시한다.
- rationale 은 한국어 한두 문장으로, 업종/품명 근거를 명시한다.
출력은 지정된 JSON 스키마를 따른다."""


class LLMRecommender(Protocol):
    def recommend(self, features: dict[str, str],
                  allowed: dict[str, int]) -> Recommendation: ...


class AnthropicRecommender:
    """Claude API 로 차변계정을 추정. client 주입 가능(테스트/대체)."""

    def __init__(self, *, client=None, model: Optional[str] = None,
                 cache_path: Optional[str] = None,
                 max_tokens: int = 1024, effort: Optional[str] = None,
                 config_dir: str | None = None):
        cfg = (load_rules(config_dir).get("recommend", {}) or {}).get("llm", {}) or {}
        self.model = model or cfg.get("model", "claude-opus-4-8")
        self.max_tokens = max_tokens
        self.effort = effort if effort is not None else cfg.get("effort")
        self._client = client
        self._cache_path = Path(cache_path) if cache_path else (
            Path(cfg["cache_path"]) if cfg.get("cache_path") else None)
        self._cache: dict[str, dict] = {}
        if self._cache_path and self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}

    # ── client ──
    def _get_client(self):
        if self._client is None:
            import anthropic  # 지연 import: SDK 는 LLM 사용 시에만 필요
            self._client = anthropic.Anthropic()
        return self._client

    def _schema(self, names: list[str]) -> dict:
        return {
            "type": "object",
            "properties": {
                "account_name": {"type": "string", "enum": names},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "alternatives": {"type": "array",
                                 "items": {"type": "string", "enum": names}},
            },
            "required": ["account_name", "confidence", "rationale"],
            "additionalProperties": False,
        }

    def recommend(self, features: dict[str, str],
                  allowed: dict[str, int]) -> Recommendation:
        sig = feature_signature(features)
        if sig in self._cache:
            c = self._cache[sig]
            return Recommendation(c["account_code"], c["confidence"],
                                  c["basis"], list(c.get("alternatives", [])),
                                  resolved=c["account_code"] is not None)

        names = list(allowed.keys())
        allowed_lines = "\n".join(f"- {n} (코드 {allowed[n]})" for n in names)
        user = ("허용 계정:\n" + allowed_lines
                + "\n\n분류 대상 특징(JSON):\n"
                + json.dumps(features, ensure_ascii=False))

        kwargs = dict(
            model=self.model, max_tokens=self.max_tokens, system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema",
                                      "schema": self._schema(names)}},
        )
        if self.effort:
            kwargs["output_config"]["effort"] = self.effort

        resp = self._get_client().messages.create(**kwargs)
        text = next((b.text for b in resp.content
                     if getattr(b, "type", None) == "text"), None)
        if not text:
            return Recommendation(None, 0.0, "LLM 응답 없음 → 담당자 확인", resolved=False)

        data = json.loads(text)
        name = data.get("account_name")
        code = allowed.get(name)
        if code is None:
            return Recommendation(None, 0.0,
                                  f"LLM 선택 계정 미허용: {name!r}", resolved=False)
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        alts = [allowed[a] for a in (data.get("alternatives") or []) if a in allowed]
        rec = Recommendation(code, round(conf, 3),
                             "LLM 추정: " + (data.get("rationale") or "").strip(),
                             alts, resolved=True)

        self._cache[sig] = {"account_code": code, "confidence": rec.confidence,
                            "basis": rec.basis, "alternatives": alts}
        self._flush()
        return rec

    def _flush(self) -> None:
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2), "utf-8")


def llm_available() -> bool:
    """API 키가 환경에 있으면 LLM 추정 가능."""
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def allowed_accounts(config_dir: str | None = None) -> dict[str, int]:
    """추정에 허용할 계정명→코드. 상대계정(미지급비용 등)은 차변 후보에서 제외."""
    mapping = dict(load_account_codes(config_dir))
    # 대변/자산성 상대계정은 차변 비용계정 후보가 아니므로 제외(혼동 방지).
    for exclude in ("미지급비용", "미지급금", "외상매입금", "선급금", "현금", "가지급금"):
        mapping.pop(exclude, None)
    return mapping
