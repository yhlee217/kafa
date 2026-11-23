"""호스트 Claude 추천을 운영 파이프라인에 붙인다 — 질문지 ↔ 답지 왕복(비-PII).

MCP `analyze`→`convert` 는 파일 하나·행 번호 단위라 운영(수임처 수십 곳, 매달)에 맞지
않는다. 여기서는 **특징 서명**(업태/종목/품명/유형) 단위로 묻고 답을 모아 둔다. 같은 특징은
수임처·달이 바뀌어도 같은 답을 쓴다 — 한 번 물은 것은 다시 묻지 않는다.

    <결과폴더>/_ai/questions.json   질문지 — 특징·건수·계정 목록만. Claude 가 읽는다
    <결과폴더>/_ai/answers.json     답지 — Claude 가 쓴다
    <결과폴더>/_ai/picks.json       답 모음(누적) — 파이프라인이 적용한다

보안 제0원칙: 질문지에는 거래처·사업자번호·금액·날짜가 **없다**(`make_features` 가 구조적으로
빼고, 품명의 가맹점명·긴 숫자는 `scrub_item` 이 가린다). 답은 허용 계정코드만 채택한다.

순서: 이력·시드(그 수임처가 처리해 온 방식)가 먼저다. Claude 답은 그것이 못 푼 행에만 쓴다.
결정 배경은 docs/decisions.md, 쓰는 법은 docs/cowork_ask.md.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from kafa.recommend.features import feature_signature, make_features
from kafa.recommend.recommender import Recommendation, Recommender
from kafa.rules.models import InputRow, Verdict

AI_DIR = "_ai"
QUESTIONS = "questions.json"
ANSWERS = "answers.json"
PICKS = "picks.json"
EXAM_QUESTIONS = "exam_questions.json"
EXAM_ANSWERS = "exam_answers.json"
EXAM_KEY = "exam_key.json"           # 정답 — _ai 밖(<결과폴더>/_exam/)에 둔다
KEY_DIR = "_exam"                    # Cowork 에 _ai 폴더만 연결하면 정답은 보이지 않는다
EXAM_REPORT = "exam_report.txt"

BASIS_PREFIX = "AI(Claude) 추정: "
HINT_PREFIX = "AI(Claude) 메모: "     # 적용하지 않은 답(모름·낮은 확신) — 담당자 참고용
FEATURE_KEYS = ("업태", "종목", "품명", "유형")
_UNKNOWN = {"", "모름", "unknown", "none", "null", "-"}
_MARKER = re.compile(r"^[\(\[]\s*(제|판|도|관|분)\s*[\)\]]")
_LONG_DIGITS = re.compile(r"\d[\d\- ]{4,}\d")

HOW_TO_ANSWER = (
    "각 질문의 업태·종목·품명·유형만 보고, 이 카드 결제가 회계상 어떤 비용(차변계정)인지 "
    "accounts 에서 하나 고르세요. 거래처·사업자번호·금액은 일부러 빼 두었습니다. "
    "답은 같은 폴더의 answer_file 에 "
    '{"answers": [{"id": "<질문 id>", "code": <계정코드>, "confidence": 0~1, '
    '"why": "한 줄 근거"}]} 형식으로 쓰세요. '
    "판단이 서지 않으면 code 를 null 로 두세요(모름). 추측으로 채우지 마세요 — "
    "모름은 담당자가 직접 봅니다."
)


# ── 설정·경로 ──

def settings(config_dir: str | None = None) -> dict:
    from kafa.config_loader import load_rules
    cfg = ((load_rules(config_dir).get("recommend") or {}).get("host_picks") or {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "min_confidence": float(cfg.get("min_confidence", 0.5)),
        "confidence_cap": float(cfg.get("confidence_cap", 0.7)),
        "ask_before_run": bool(cfg.get("ask_before_run", True)),
    }


def ai_dir(out_dir) -> Path:
    return Path(out_dir) / AI_DIR


def exam_key_path(out_dir) -> Path:
    return Path(out_dir) / KEY_DIR / EXAM_KEY


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없거나 깨졌으면 기본값
        return default


def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── 특징·서명 ──

def pick_signature(feats: dict, *, config_dir: str | None = None) -> str:
    """질문·답의 키. 업태·종목은 표기 통일(config/industry.yaml)을 거쳐 같은 업종이 한 질문이 된다."""
    from kafa.recommend.seed import canonical_industry
    return feature_signature({
        "업태": canonical_industry(feats.get("업태", ""), "업태", config_dir=config_dir),
        "종목": canonical_industry(feats.get("종목", ""), "종목", config_dir=config_dir),
        "품명": " ".join((feats.get("품명") or "").split()),
        "유형": (feats.get("유형") or "").strip(),
    })


def has_info(feats: dict) -> bool:
    """물어볼 거리가 있나 — 업태·종목·품명이 다 비면(가려진 것 포함) Claude 도 모른다."""
    item = " ".join(t for t in (feats.get("품명") or "").split()
                    if t not in ("(가맹점)", "#"))
    return bool((feats.get("업태") or "").strip() or (feats.get("종목") or "").strip()
                or item)


def _is_agent(거래처: str, 업태: str, 종목: str, config_dir) -> bool:
    """결제대행사 행 — 업태·종목이 대행사의 것이라 물어봐야 소용없다(되찾기 흐름으로 간다)."""
    from kafa.rules.agents import agent_of
    return agent_of(거래처 or "", 업태=업태 or "", 종목=종목 or "",
                    config_dir=config_dir) is not None


# ── 계정 목록 ──

def account_names(config_dir: str | None = None) -> dict[int, str]:
    """허용 차변계정 {코드: 이름} — 상대계정(대변 쪽)은 빠져 있다."""
    from kafa.recommend.llm import allowed_accounts
    return {code: name for name, code in allowed_accounts(config_dir).items()}


def account_catalog(usage: dict | None = None, *,
                    config_dir: str | None = None) -> list[dict]:
    """질문지에 싣는 계정 목록. 처리 이력이 있으면 **이 사무실이 실제로 쓰는 계정**만,
    많이 쓴 순서로(같은 이름의 (제)/(판) 중 무엇을 쓰는지가 여기서 드러난다)."""
    names = account_names(config_dir)
    if usage:
        rows = [{"code": c, "name": names[c], "used": n}
                for c, n in sorted(usage.items(), key=lambda kv: (-kv[1], kv[0]))
                if c in names]
        if rows:
            return rows
    return [{"code": c, "name": n} for c, n in sorted(names.items())]


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def _core(name: str) -> str:
    """(제)/(판) 같은 구분 표시를 뗀 계정 이름."""
    return _MARKER.sub("", _norm_name(name))


def resolve_code(answer: dict, names: dict[int, str]) -> tuple[int | None, str]:
    """답 한 줄 → (코드 또는 None=모름, 오류 사유). 허용 계정이 아니면 오류."""
    raw = None
    for k in ("code", "account_code", "account"):
        if k in answer:
            raw = answer[k]
            break
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in _UNKNOWN):
        return None, ""
    if isinstance(raw, bool):
        return None, "계정코드 형식 오류"
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.strip().isdigit()):
        code = int(raw)
        return (code, "") if code in names else (None, f"허용되지 않은 계정코드 {code}")
    by_name = {_norm_name(n): c for c, n in names.items()}
    code = by_name.get(_norm_name(str(raw)))
    return (code, "") if code is not None else (None, "목록에 없는 계정명")


def _confidence(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, v)), 3)


def _clean_why(text) -> str:
    t = " ".join(str(text or "").split())
    return _LONG_DIGITS.sub("#", t)[:120]


def _parse_answers(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("answers", [])
    return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []


# ── 답 모음(picks.json) ──

def load_picks(out_dir) -> dict[str, dict]:
    data = _read_json(ai_dir(out_dir) / PICKS, {})
    picks = data.get("picks") if isinstance(data, dict) else None
    return dict(picks) if isinstance(picks, dict) else {}


def save_picks(out_dir, picks: dict[str, dict]) -> Path:
    return _write_json(ai_dir(out_dir) / PICKS,
                       {"kind": "kafa-picks", "version": 1, "picks": picks})


@dataclass
class MergeResult:
    accepted: int = 0                    # 계정을 고른 답
    unknown: int = 0                     # '모름' — 다시 묻지 않고 담당자에게 남긴다
    rejected: list[tuple[str, str]] = field(default_factory=list)
    archived: Path | None = None      # 처리한 답지를 옮겨 둔 곳

    def line(self) -> str:
        s = f"Claude 답 반영: 계정 {self.accepted}건 · 모름 {self.unknown}건"
        if self.rejected:
            s += f" · 받지 않음 {len(self.rejected)}건"
        return s


def merge_answers(out_dir, *, config_dir: str | None = None) -> MergeResult | None:
    """answers.json 을 검증해 picks.json 에 합친다. 답지가 없으면 None.

    질문지에 없는 id·허용되지 않은 계정은 받지 않는다(이유를 남긴다). 처리한 답지는
    answers.<시각>.json 으로 옮겨 다시 합쳐지지 않게 한다.
    """
    d = ai_dir(out_dir)
    path = d / ANSWERS
    if not path.exists():
        return None
    res = MergeResult()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        res.rejected.append(("(답지)", f"JSON 형식 오류: {type(e).__name__}"))
        return res                       # 고쳐서 다시 — 옮기지 않는다
    questions = {q.get("id"): q for q in
                 (_read_json(d / QUESTIONS, {}).get("questions") or [])
                 if isinstance(q, dict)}
    names = account_names(config_dir)
    picks = load_picks(out_dir)
    for a in _parse_answers(data):
        qid = str(a.get("id") or "").strip()
        q = questions.get(qid)
        if q is None:
            res.rejected.append((qid or "(id 없음)", "질문지에 없는 id"))
            continue
        code, err = resolve_code(a, names)
        if err:
            res.rejected.append((qid, err))
            continue
        picks[qid] = {
            **{k: q.get(k, "") for k in FEATURE_KEYS},
            "rows": q.get("rows", 0),
            "code": code,
            "name": names.get(code) if code is not None else None,
            "confidence": _confidence(a.get("confidence"), 0.6 if code is not None else 0.0),
            "why": _clean_why(a.get("why") or a.get("rationale")),
            "answered": _today(),
        }
        if code is None:
            res.unknown += 1
        else:
            res.accepted += 1
    save_picks(out_dir, picks)
    res.archived = path.with_name(f"answers.{datetime.now():%Y%m%d-%H%M%S}.json")
    path.rename(res.archived)
    return res


# ── 파이프라인 적용 ──

class HostPickRecommender(Recommender):
    """이력·시드(primary)가 못 푼 행에 Claude 답(picks)을 적용한다.

    허용 계정·최소 확신을 넘는 답만 채택하고, 신뢰도는 상한으로 누른다(자기보고 확신은
    보정되지 않았다). 대행사 행에는 쓰지 않는다.
    """

    def __init__(self, picks: dict[str, dict], primary: Recommender | None = None, *,
                 min_confidence: float = 0.5, confidence_cap: float = 0.7,
                 config_dir: str | None = None):
        self.picks = picks or {}
        self.primary = primary
        self.allowed = set(account_names(config_dir))
        self.min_confidence = min_confidence
        self.cap = confidence_cap
        self.config_dir = config_dir
        self.applied = 0

    def _pick(self, row: InputRow) -> dict | None:
        feats = make_features(업태=row.업태, 종목=row.종목, 품명=row.품명, 유형=row.유형,
                              거래처=row.거래처)
        if not has_info(feats) or _is_agent(row.거래처, row.업태, row.종목, self.config_dir):
            return None
        return self.picks.get(pick_signature(feats, config_dir=self.config_dir))

    def _hint(self, p: dict) -> str:
        """적용하지 않은 답을 담당자가 볼 수 있게 한 줄로(미해소 행의 근거 칸)."""
        why = p.get("why") or ""
        if p.get("code") is None:
            return HINT_PREFIX + "모름" + (f" — {why}" if why else "")
        conf = float(p.get("confidence") or 0)
        return (HINT_PREFIX + f"{p.get('name') or p.get('code')} (확신 {conf:.1f} — 적용 안 함)"
                + (f" — {why}" if why else ""))

    def recommend_for(self, row: InputRow) -> Recommendation:
        rec = (self.primary.recommend_for(row) if self.primary is not None
               else Recommendation(None, 0.0, "추천 없음 → 담당자 확인", resolved=False))
        if rec.resolved or not self.picks:
            return rec
        p = self._pick(row)
        if p is None:
            return rec
        if (p.get("code") not in self.allowed
                or float(p.get("confidence") or 0) < self.min_confidence):
            return Recommendation(None, 0.0, self._hint(p), resolved=False)
        self.applied += 1
        conf = min(float(p.get("confidence") or 0), self.cap)
        why = p.get("why") or f"{p.get('업태', '')}/{p.get('종목', '')}"
        return Recommendation(int(p["code"]), round(conf, 3), BASIS_PREFIX + why,
                              resolved=True)


def wrap(recommender: Recommender, picks: dict, *,
         config_dir: str | None = None) -> Recommender:
    """설정이 켜져 있고 답이 있으면 Claude 답을 붙인 추천기, 아니면 그대로."""
    s = settings(config_dir)
    if not s["enabled"] or not picks:
        return recommender
    return HostPickRecommender(picks, recommender, min_confidence=s["min_confidence"],
                               confidence_cap=s["confidence_cap"], config_dir=config_dir)


# ── 질문지 ──

@dataclass
class QuestionSet:
    questions: dict[str, dict] = field(default_factory=dict)  # 서명 → 질문
    unresolved_rows: int = 0     # 이력·시드·기존 답으로도 못 푼 행
    asked_rows: int = 0          # 이미 물었는데 '모름'·낮은 확신이라 남은 행
    no_info_rows: int = 0        # 업태·종목·품명이 비어 물어볼 거리가 없는 행
    agent_rows: int = 0          # 결제대행사 행(되찾기 흐름으로)

    @property
    def new_rows(self) -> int:
        return sum(q["rows"] for q in self.questions.values())

    def add(self, classified, picks: dict, *, config_dir: str | None = None) -> None:
        for c in classified:
            if c.skipped or c.source is None:
                continue
            if c.판정유형 != Verdict.UNRESOLVED or c.차변계정코드 is not None:
                continue
            row = c.source
            self.unresolved_rows += 1
            if c.is_agent or _is_agent(row.거래처, row.업태, row.종목, config_dir):
                self.agent_rows += 1
                continue
            feats = make_features(업태=row.업태, 종목=row.종목, 품명=row.품명,
                                  유형=row.유형, 거래처=row.거래처)
            if not has_info(feats):
                self.no_info_rows += 1
                continue
            sig = pick_signature(feats, config_dir=config_dir)
            if sig in picks:
                self.asked_rows += 1
                continue
            q = self.questions.get(sig)
            if q is None:
                q = self.questions[sig] = {"id": sig, **feats, "rows": 0}
            q["rows"] += 1

    def lines(self) -> list[str]:
        return [
            f"미해소 {self.unresolved_rows}건 중",
            f"  새로 물을 것 {len(self.questions)}가지 ({self.new_rows}건)",
            f"  이미 물었지만 '모름'·낮은 확신 {self.asked_rows}건",
            f"  업태·종목·품명이 비어 물을 거리 없음 {self.no_info_rows}건",
            f"  결제대행사 {self.agent_rows}건 (kafa-lookup 되찾기로)",
        ]


def write_questions(out_dir, questions: dict[str, dict], catalog: list[dict], *,
                    name: str = QUESTIONS, answer_file: str = ANSWERS,
                    kind: str = "kafa-questions") -> Path:
    items = sorted(questions.values(),
                   key=lambda q: (-q.get("rows", 0), q["업태"], q["종목"], q["품명"], q["유형"]))
    return _write_json(ai_dir(out_dir) / name, {
        "kind": kind, "version": 1, "created": _today(),
        "how_to_answer": HOW_TO_ANSWER, "answer_file": answer_file,
        "accounts": catalog, "questions": items,
    })


# ── 시험(정확도 기준선) ──

def build_exam(records, out_dir, *, n: int = 150, usage: dict | None = None,
               config_dir: str | None = None, rng_seed: int = 0) -> int:
    """처리된 행(정답이 있는 행)으로 시험지를 만든다. 반은 많이 나온 특징, 반은 무작위.

    records: [(거래처, 업태, 종목, 품명, 유형, 차변코드)] — VoucherStore.account_records.
    정답(exam_key.json)은 로컬에만 두고, 시험지(exam_questions.json)는 질문지와 같은 형식이다.
    반환: 문제 수.
    """
    names = account_names(config_dir)
    agg: dict[str, dict] = {}
    for 거래처, 업태, 종목, 품명, 유형, code in records:
        if code not in names or _is_agent(거래처, 업태, 종목, config_dir):
            continue
        feats = make_features(업태=업태, 종목=종목, 품명=품명, 유형=유형, 거래처=거래처)
        if not has_info(feats):
            continue
        sig = pick_signature(feats, config_dir=config_dir)
        e = agg.setdefault(sig, {"feats": feats, "codes": Counter()})
        e["codes"][code] += 1
    ranked = sorted(agg, key=lambda s: (-sum(agg[s]["codes"].values()), s))
    half = n // 2
    chosen = ranked[:half]
    rest = ranked[half:]
    random.Random(rng_seed).shuffle(rest)
    chosen += rest[: n - len(chosen)]

    questions, key = {}, {}
    for sig in chosen:
        e = agg[sig]
        rows = sum(e["codes"].values())
        questions[sig] = {"id": sig, **e["feats"], "rows": rows}
        key[sig] = {"codes": {str(c): k for c, k in e["codes"].most_common()},
                    "answer": e["codes"].most_common(1)[0][0], "rows": rows}
    write_questions(out_dir, questions, account_catalog(usage, config_dir=config_dir),
                    name=EXAM_QUESTIONS, answer_file=EXAM_ANSWERS, kind="kafa-exam")
    _write_json(exam_key_path(out_dir), {"kind": "kafa-exam-key", "key": key})
    legacy = ai_dir(out_dir) / EXAM_KEY  # 예전 위치(공유 폴더 안) — 남겨 두지 않는다
    if legacy.exists():
        legacy.unlink()
    return len(questions)


@dataclass
class ExamScore:
    questions: int = 0
    answered: int = 0
    unknown: int = 0
    exact: int = 0               # 최빈 정답과 같은 코드
    same_account: int = 0        # (제)/(판) 구분만 다르고 계정은 같음
    seen: int = 0                # 이력에 있는 계정 중 하나(사람마다 갈리는 업종)
    rows: int = 0
    rows_exact: int = 0
    # 행 단위 — 그 행을 실제로 처리한 계정과 같은 행 수. 최빈 기준은 다른 계정으로 처리한
    # 수임처의 행까지 '정답'으로 세어 부풀려진다. 사무실 이력 추천(kafa-ask prior)과 같은 잣대.
    rows_hit: int = 0
    min_confidence: float = 0.5
    applied_rows: int = 0
    applied_hit: int = 0
    bands: dict = field(default_factory=dict)       # 확신 구간 → [맞음, 전체]
    band_rows: dict = field(default_factory=dict)   # 확신 구간 → [행 맞음, 행 전체]
    misses: list = field(default_factory=list)      # (특징, 답, 정답) — 비-PII
    rejected: list = field(default_factory=list)

    def rate(self, k: int) -> str:
        return f"{k / self.answered:.0%}" if self.answered else "-"

    def render(self) -> str:
        out = [
            "Claude 추천 시험 — 처리된 행(정답 있음)으로 잰 정확도",
            f"문제 {self.questions}가지 · 답함 {self.answered} · 모름 {self.unknown}"
            + (f" · 받지 않음 {len(self.rejected)}" if self.rejected else ""),
            "",
            f"정답(최빈 계정과 같음)        {self.exact}/{self.answered} ({self.rate(self.exact)})",
            f"계정은 같고 (제)/(판)만 다름   {self.same_account}",
            f"이력에 있는 다른 계정(갈리는 업종) {self.seen}",
            (f"틀림                          "
             f"{self.answered - self.exact - self.same_account - self.seen}"),
            "건수 가중(최빈 기준)           "
            + (f"{self.rows_exact / self.rows:.0%} ({self.rows_exact}/{self.rows}건)"
               if self.rows else "-"),
            "",
            "확신 구간별 정답률(문제 단위, 최빈 기준):",
        ]
        for band in ("0.8 이상", "0.5~0.8", "0.5 미만"):
            ok, tot = self.bands.get(band, (0, 0))
            out.append(f"  {band:<8} {ok}/{tot}" + (f" ({ok / tot:.0%})" if tot else ""))
        out += [
            "",
            "행 단위 — 그 행을 실제로 처리한 계정과 같은 비율(수임처마다 갈린 것까지 반영,",
            "          kafa-ask prior 와 같은 잣대):",
            f"  답한 것 전체               {_frac(self.rows_hit, self.rows)}",
            (f"  적용되는 답(확신 {self.min_confidence} 이상)  "
             f"{_frac(self.applied_hit, self.applied_rows)}"),
        ]
        for band in ("0.8 이상", "0.5~0.8", "0.5 미만"):
            ok, tot = self.band_rows.get(band, (0, 0))
            out.append(f"  {band:<8} {_frac(ok, tot)}")
        if self.misses:
            out += ["", "틀린 예(업태 / 종목 / 품명 / 유형 → 답 · 정답):"]
            for f, got, want in self.misses[:20]:
                out.append(f"  {f['업태'] or '-'} / {f['종목'] or '-'} / {f['품명'] or '-'} / "
                           f"{f['유형'] or '-'} → {got} · {want}")
        return "\n".join(out) + "\n"


def _frac(a: int, b: int) -> str:
    return f"{a}/{b}건 ({a / b:.0%})" if b else "-"


def score_exam(out_dir, *, config_dir: str | None = None) -> ExamScore | None:
    d = ai_dir(out_dir)
    key_file = exam_key_path(out_dir)
    if not key_file.exists():
        key_file = d / EXAM_KEY          # 예전 위치에 만든 시험
    key = (_read_json(key_file, {}) or {}).get("key") or {}
    if not key or not (d / EXAM_ANSWERS).exists():
        return None
    questions = {q["id"]: q for q in
                 (_read_json(d / EXAM_QUESTIONS, {}).get("questions") or [])}
    names = account_names(config_dir)
    sc = ExamScore(questions=len(key), min_confidence=settings(config_dir)["min_confidence"])
    for a in _parse_answers(_read_json(d / EXAM_ANSWERS, {})):
        qid = str(a.get("id") or "").strip()
        k = key.get(qid)
        if k is None:
            sc.rejected.append((qid, "시험지에 없는 id"))
            continue
        code, err = resolve_code(a, names)
        if err:
            sc.rejected.append((qid, err))
            continue
        if code is None:
            sc.unknown += 1
            continue
        sc.answered += 1
        want = int(k["answer"])
        rows = int(k.get("rows") or 0)
        sc.rows += rows
        conf = _confidence(a.get("confidence"), 0.6)
        band = "0.8 이상" if conf >= 0.8 else ("0.5~0.8" if conf >= 0.5 else "0.5 미만")
        hit = int((k.get("codes") or {}).get(str(code), 0))   # 실제로 이 계정이었던 행
        sc.rows_hit += hit
        if conf >= sc.min_confidence:
            sc.applied_rows += rows
            sc.applied_hit += hit
        rok, rtot = sc.band_rows.get(band, (0, 0))
        sc.band_rows[band] = (rok + hit, rtot + rows)
        ok, tot = sc.bands.get(band, (0, 0))
        if code == want:
            sc.exact += 1
            sc.rows_exact += rows
            sc.bands[band] = (ok + 1, tot + 1)
            continue
        sc.bands[band] = (ok, tot + 1)
        if _core(names.get(code, "")) == _core(names.get(want, "")):
            sc.same_account += 1
        elif str(code) in (k.get("codes") or {}):
            sc.seen += 1
        else:
            q = questions.get(qid) or {}
            sc.misses.append(({f: q.get(f, "") for f in FEATURE_KEYS},
                              names.get(code, str(code)), names.get(want, str(want))))
    (d / EXAM_REPORT).write_text(sc.render(), encoding="utf-8")
    return sc
