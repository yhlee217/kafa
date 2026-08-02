"""처리 이력 → 규칙 추정.

입력은 다운로드본의 InputRow 목록(여러 파일·여러 달을 모을수록 정확). 위하고가 이미
채운 행만 학습 대상으로 삼고, 보류 항목별로 근거(건수·비율)와 추정 결론을 낸다.

보안 제0원칙: 거래처 실명·사업자번호는 어떤 산출물에도 포함하지 않는다.
비-PII 필드(계정명·대변계정·업태·종목·유형·구분·국세청·금액집계)만 사용한다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from kafa.rules.accounts import map_account_name_to_code

# 이 모듈이 사용할 수 있는 필드(=비-PII). 거래처/사업자등록번호는 의도적으로 제외.
SAFE_FIELDS = ("차변계정", "대변계정", "업태", "종목", "유형", "구분", "국세청",
               "품명", "공급가액", "세액", "비과세", "합계", "전표상태")

# 개인사업자 상대계정 후보로 볼 만한 계정명 조각(보류 항목 1)
_INDIVIDUAL_HINTS = ("인출금", "자본금", "가지급금", "대표", "사업주")


@dataclass
class Observation:
    """한 주제에 대한 추정 결과. 자동 적용 금지 — 근거와 함께 사람이 판단한다."""
    topic: str                                   # 주제
    finding: str                                 # 한 줄 결론(추정)
    evidence: list[str] = field(default_factory=list)   # 근거 라인(건수·비율)
    support: int = 0                             # 근거 건수
    confidence: float = 0.0                      # 0~1 (최빈값 비율 기반)
    pending: str = ""                            # 연결된 보류 항목

    @property
    def conclusive(self) -> bool:
        """근거가 충분하고 편중이 뚜렷한가(제안에 올릴 수준인가)."""
        return self.support > 0 and self.confidence >= 0.8


@dataclass
class InferenceReport:
    total_rows: int = 0
    learned_rows: int = 0                        # 차변계정이 채워진 행(학습 대상)
    observations: list[Observation] = field(default_factory=list)
    unmapped_accounts: list[tuple[str, int]] = field(default_factory=list)
    industry_accounts: dict[str, tuple[str, int, float]] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.learned_rows / self.total_rows if self.total_rows else 0.0


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────

def _s(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ""


def _dist_lines(counter: Counter, *, top: int = 6, unit: str = "건") -> list[str]:
    total = sum(counter.values())
    if not total:
        return []
    return [f"{name or '(빈값)'}: {n}{unit} ({n / total:.0%})"
            for name, n in counter.most_common(top)]


def _top_ratio(counter: Counter) -> tuple[str, int, float]:
    total = sum(counter.values())
    if not total:
        return "", 0, 0.0
    name, n = counter.most_common(1)[0]
    return name, n, n / total


# ── 주제별 추정 ────────────────────────────────────────────────────────

def _obs_counterparty(rows) -> Observation:
    """보류1 — 개인사업자 상대계정. 대변계정 컬럼 분포에서 실제 사용값을 본다."""
    c = Counter(_s(r.대변계정) for r in rows if _s(r.대변계정))
    if not c:
        return Observation(
            "상대계정(대변)", "대변계정이 채워진 행이 없어 추정 불가", [],
            0, 0.0, "개인사업자 상대계정")
    top, n, ratio = _top_ratio(c)
    hits = [name for name in c if any(h in name for h in _INDIVIDUAL_HINTS)]
    if hits:
        finding = (f"개인사업자 상대계정 후보 발견: {', '.join(hits)} "
                   f"— 실제 사용 이력 있음")
    else:
        finding = (f"대변은 '{top}' 위주({ratio:.0%}). 인출금 등 개인 계정 이력 없음 "
                   f"→ 법인 처리로 보이며 개인 건은 별도 확인 필요")
    return Observation("상대계정(대변)", finding, _dist_lines(c),
                       sum(c.values()), ratio, "개인사업자 상대계정")


def _obs_tax_free(rows) -> Observation:
    """보류2 — 면세(카면) 실제 처리. 카면 행의 차변계정·국세청 분포."""
    free = [r for r in rows if "면" in _s(r.유형)]
    if not free:
        return Observation("면세(카면) 처리", "카면 등 면세 유형 행이 없어 추정 불가",
                           [], 0, 0.0, "카면 처리·식당 의제")
    acc = Counter(_s(r.차변계정) for r in free if _s(r.차변계정))
    nts = Counter(_s(r.국세청) for r in free)
    biz = Counter(_s(r.업태) for r in free if _s(r.업태))
    top, n, ratio = _top_ratio(acc)
    food = sum(v for k, v in biz.items() if "음식" in k or "식당" in k)
    finding = (f"면세 {len(free)}건 — 차변은 '{top}' 위주({ratio:.0%})"
               if top else f"면세 {len(free)}건 — 차변계정 미기재")
    if food:
        finding += f" · 음식점업 {food}건(의제매입 검토 대상)"
    ev = (["차변계정 " + " / ".join(_dist_lines(acc, top=4))] if acc else []) \
        + (["국세청 " + " / ".join(_dist_lines(nts, top=4))] if nts else []) \
        + (["업태 " + " / ".join(_dist_lines(biz, top=4))] if biz else [])
    return Observation("면세(카면) 처리", finding, ev, len(free), ratio,
                       "카면 처리·식당 의제")


def _obs_simplified(rows) -> Observation:
    """보류3 — 간이과세자 식별자. '구분' 컬럼에 식별 가능한 값이 있는지."""
    c = Counter(_s(r.구분) for r in rows if _s(r.구분))
    if not c:
        return Observation("간이과세 식별", "'구분' 값이 비어 있어 식별자 확인 불가",
                           [], 0, 0.0, "간이과세 자동 불공제")
    hit = [k for k in c if "간이" in k]
    if hit:
        finding = f"'구분'에서 간이과세 식별 가능: {', '.join(hit)}"
        conf = 1.0
    else:
        finding = (f"'구분'은 {', '.join(list(c)[:4])} 뿐 — "
                   f"간이과세를 이 컬럼으로는 구분할 수 없음")
        conf = 0.0
    return Observation("간이과세 식별", finding, _dist_lines(c),
                       sum(c.values()), conf, "간이과세 자동 불공제")


def _obs_service_charge(rows) -> Observation:
    """보류4 — 봉사료=비과세 동일성. 비과세 금액이 실제로 쓰이는지·어디서."""
    nz = [r for r in rows if Decimal(r.비과세 or 0) > 0]
    if not nz:
        return Observation(
            "봉사료(비과세)", "비과세 금액이 있는 행이 없음 — 현행 매핑을 검증할 근거 없음",
            [], 0, 0.0, "봉사료=비과세 동일성")
    biz = Counter(_s(r.업태) for r in nz if _s(r.업태))
    top, n, ratio = _top_ratio(biz)
    finding = (f"비과세 금액 사용 {len(nz)}건"
               + (f" — 업태 '{top}' 집중({ratio:.0%})" if top else ""))
    if top and ("음식" in top or "숙박" in top):
        finding += " → 봉사료 성격일 가능성 높음(확인 필요)"
    return Observation("봉사료(비과세)", finding, _dist_lines(biz),
                       len(nz), ratio, "봉사료=비과세 동일성")


def _obs_deduction(rows) -> Observation:
    """국세청 컬럼 값 분포 — 공제 판정 기준이 실제로 어떤 값으로 오는지."""
    c = Counter(_s(r.국세청) for r in rows)
    top, n, ratio = _top_ratio(c)
    return Observation("공제 판정(국세청 값)",
                       f"최다값 '{top}' — {ratio:.0%}" if top else "값 없음",
                       _dist_lines(c), sum(c.values()), ratio)


def _collect_unmapped(rows, *, config_dir) -> list[tuple[str, int]]:
    """코드로 매핑되지 않는 차변계정명 — 계정과목 시트로 채워야 할 목록."""
    c: Counter = Counter()
    for r in rows:
        name = _s(r.차변계정)
        if not name:
            continue
        if map_account_name_to_code(name, config_dir=config_dir).value is None:
            c[name] += 1
    return c.most_common()


def _collect_industry(rows, *, min_support: int, min_ratio: float
                      ) -> dict[str, tuple[str, int, float]]:
    """업태 → 최빈 차변계정. 근거·편중이 충분한 것만(룰 후보)."""
    by: dict[str, Counter] = {}
    for r in rows:
        ind, acc = _s(r.업태), _s(r.차변계정)
        if ind and acc:
            by.setdefault(ind, Counter())[acc] += 1
    out: dict[str, tuple[str, int, float]] = {}
    for ind, c in by.items():
        acc, n, ratio = _top_ratio(c)
        if n >= min_support and ratio >= min_ratio:
            out[ind] = (acc, n, ratio)
    return dict(sorted(out.items(), key=lambda kv: -kv[1][1]))


# ── 공개 API ──────────────────────────────────────────────────────────

def infer_rules(rows, *, config_dir: str | None = None,
                min_support: int = 5, min_ratio: float = 0.8) -> InferenceReport:
    """처리 이력(InputRow 목록) → 보류 항목별 추정 리포트.

    학습 대상은 위하고가 이미 채운 행(차변계정 보유). 여러 파일·여러 달을 합쳐서
    넣을수록 근거가 두터워진다. 결과는 추정이며 자동 적용하지 않는다.
    """
    rows = list(rows or [])
    learned = [r for r in rows if _s(r.차변계정)]

    rep = InferenceReport(total_rows=len(rows), learned_rows=len(learned))
    rep.observations = [
        _obs_counterparty(rows),
        _obs_tax_free(rows),
        _obs_simplified(rows),
        _obs_service_charge(rows),
        _obs_deduction(rows),
    ]
    rep.unmapped_accounts = _collect_unmapped(learned, config_dir=config_dir)
    rep.industry_accounts = _collect_industry(
        learned, min_support=min_support, min_ratio=min_ratio)
    return rep


def render_inference(rep: InferenceReport) -> str:
    """담당자용 텍스트 리포트(비-PII)."""
    L = ["── 처리 이력 기반 규칙 추정 ──",
         f"전체 {rep.total_rows}건 중 학습 대상(이미 분류됨) {rep.learned_rows}건"
         f" ({rep.coverage:.0%})",
         "※ 모두 추정입니다. 반영 전 담당자 확인이 필요합니다.", ""]

    for o in rep.observations:
        mark = "◆" if o.conclusive else "◇"
        L.append(f"{mark} {o.topic} — {o.finding}")
        if o.pending:
            L.append(f"   보류항목: {o.pending}")
        for e in o.evidence:
            L.append(f"   · {e}")
        L.append("")

    L.append(f"◆ 코드 미매핑 계정명 {len(rep.unmapped_accounts)}종"
             " — 계정과목 시트로 채워야 할 목록")
    if rep.unmapped_accounts:
        for name, n in rep.unmapped_accounts[:20]:
            L.append(f"   · {name}: {n}건")
        if len(rep.unmapped_accounts) > 20:
            L.append(f"   · … 외 {len(rep.unmapped_accounts) - 20}종")
    else:
        L.append("   · 없음(모두 매핑됨)")
    L.append("")

    L.append(f"◆ 업종별 계정 패턴 {len(rep.industry_accounts)}건(근거 충분분만)")
    if rep.industry_accounts:
        for ind, (acc, n, ratio) in list(rep.industry_accounts.items())[:15]:
            L.append(f"   · {ind} → {acc} ({n}건, {ratio:.0%})")
    else:
        L.append("   · 근거가 충분한 패턴 없음")
    return "\n".join(L)


def propose_config(rep: InferenceReport) -> dict:
    """검토용 설정 제안(dict). 그대로 반영하지 말고 담당자 확인 후 병합할 것."""
    return {
        "_주의": "kafa learn 이 이력에서 추정한 값입니다. 검토 후 반영하세요.",
        "_근거": {"학습행": rep.learned_rows, "전체행": rep.total_rows},
        "업종별_계정_힌트": {ind: acc for ind, (acc, _n, _r)
                        in rep.industry_accounts.items()},
        "코드_필요_계정명": [name for name, _n in rep.unmapped_accounts],
        "확인필요_추정": [
            {"주제": o.topic, "보류항목": o.pending, "추정": o.finding,
             "근거건수": o.support}
            for o in rep.observations if o.pending
        ],
    }
