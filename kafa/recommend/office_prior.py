"""사무실 전체 이력 추천 — 다른 수임처들이 같은 업종(업태|종목)을 어떤 계정으로 처리했나.

수임처 자기 이력(시드)으로 못 푼 행에 쓴다. 순서는 **수임처 이력 → 사무실 이력 → Claude 답**.
같은 업종이라도 수임처마다 처리가 갈리는 경우가 있다(음식점 = 복리후생비냐 접대비냐).
그래서 **여러 수임처가 같은 쪽으로 처리한 업종**에만 쓴다:

- 그 업종을 처리한 수임처가 `min_clients` 곳 이상
- 최다 계정의 건수 비중이 `min_share` 이상, **그리고** 그 계정을 주로 쓴 수임처 비중도
  `min_share` 이상(한 곳이 건수로 밀어붙인 업종을 거른다)

재료는 처리된 행(rule_confirmed)뿐이다 — 도구가 추천한 행을 다시 배우지 않는다.
켜기 전에 `kafa-ask prior` 로 한 수임처씩 빼고 맞혀 보는 정답률을 잰다(docs/decisions.md).
보안: 거래처·사업자번호는 대행사 거르기에만 로컬에서 쓰고 근거 문장에는 건수·비중만 쓴다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from kafa.recommend.recommender import Recommendation, Recommender
from kafa.rules.models import InputRow

BASIS_PREFIX = "사무실 이력: "


def settings(config_dir: str | None = None) -> dict:
    from kafa.config_loader import load_rules
    cfg = ((load_rules(config_dir).get("recommend") or {}).get("office_prior") or {})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "min_clients": int(cfg.get("min_clients", 3)),
        "min_share": float(cfg.get("min_share", 0.8)),
        "confidence_cap": float(cfg.get("confidence_cap", 0.6)),
    }


def industry_key(업태: str, 종목: str, *, config_dir: str | None = None) -> str:
    """업태·종목 둘 다 있어야 키가 된다(업태만으로는 너무 넓다 — '서비스')."""
    from kafa.recommend.seed import canonical_industry
    u = canonical_industry(업태, "업태", config_dir=config_dir)
    j = canonical_industry(종목, "종목", config_dir=config_dir)
    return f"{u}|{j}" if u and j else ""


@dataclass
class PriorHit:
    code: int
    share: float        # 건수 비중
    agree: float        # 그 계정을 주로 쓴 수임처 비중
    clients: int
    rows: int


@dataclass
class OfficePrior:
    config_dir: str | None = None
    by_key: dict[str, dict[str, Counter]] = field(default_factory=dict)  # 키 → 수임처 → 계정

    def add(self, client: str, 업태: str, 종목: str, code: int) -> None:
        k = industry_key(업태, 종목, config_dir=self.config_dir)
        if k:
            self.by_key.setdefault(k, {}).setdefault(client, Counter())[int(code)] += 1

    @property
    def empty(self) -> bool:
        return not self.by_key

    def suggest(self, 업태: str, 종목: str, *, min_clients: int = 3, min_share: float = 0.8,
                exclude_client: str | None = None) -> PriorHit | None:
        k = industry_key(업태, 종목, config_dir=self.config_dir)
        per = {c: n for c, n in (self.by_key.get(k) or {}).items() if c != exclude_client}
        if len(per) < min_clients:
            return None
        total: Counter = Counter()
        for n in per.values():
            total.update(n)
        code, top = total.most_common(1)[0]
        rows = sum(total.values())
        agree = sum(1 for n in per.values() if n.most_common(1)[0][0] == code) / len(per)
        share = top / rows
        if share < min_share or agree < min_share:
            return None
        return PriorHit(code=code, share=round(share, 3), agree=round(agree, 3),
                        clients=len(per), rows=rows)


def build_office_prior(records, *, config_dir: str | None = None) -> OfficePrior:
    """records: [(client_id, 거래처, 업태, 종목, 차변코드)] — 대행사 행은 뺀다."""
    from kafa.rules.agents import agent_of
    prior = OfficePrior(config_dir=config_dir)
    for client, 거래처, 업태, 종목, code in records:
        if agent_of(거래처 or "", 업태=업태 or "", 종목=종목 or "", config_dir=config_dir):
            continue
        prior.add(client, 업태, 종목, code)
    return prior


class OfficePriorRecommender(Recommender):
    """수임처 이력(primary)이 못 푼 행에 사무실 전체 이력을 쓴다. 대행사 행 제외."""

    def __init__(self, prior: OfficePrior, primary: Recommender | None = None, *,
                 min_clients: int = 3, min_share: float = 0.8,
                 confidence_cap: float = 0.6, config_dir: str | None = None):
        self.prior = prior
        self.primary = primary
        self.min_clients = min_clients
        self.min_share = min_share
        self.cap = confidence_cap
        self.config_dir = config_dir

    def recommend_for(self, row: InputRow) -> Recommendation:
        from kafa.rules.agents import agent_of
        rec = (self.primary.recommend_for(row) if self.primary is not None
               else Recommendation(None, 0.0, "추천 없음 → 담당자 확인", resolved=False))
        if rec.resolved or self.prior.empty:
            return rec
        if agent_of(row.거래처 or "", 업태=row.업태 or "", 종목=row.종목 or "",
                    config_dir=self.config_dir):
            return rec
        hit = self.prior.suggest(row.업태, row.종목, min_clients=self.min_clients,
                                 min_share=self.min_share)
        if hit is None:
            return rec
        basis = (BASIS_PREFIX + f"같은 업종을 다른 수임처 {hit.clients}곳이 처리한 {hit.rows}건 중 "
                 f"{hit.share:.0%}가 이 계정")
        return Recommendation(hit.code, round(min(hit.share, self.cap), 3), basis,
                              resolved=True)


def wrap(recommender: Recommender, prior: OfficePrior | None, *,
         config_dir: str | None = None) -> Recommender:
    s = settings(config_dir)
    if not s["enabled"] or prior is None or prior.empty:
        return recommender
    return OfficePriorRecommender(prior, recommender, min_clients=s["min_clients"],
                                  min_share=s["min_share"],
                                  confidence_cap=s["confidence_cap"], config_dir=config_dir)


# ── 측정: 한 수임처씩 빼고 맞혀 보기 ──

GRID = ((2, 0.7), (3, 0.7), (3, 0.8), (3, 0.9), (5, 0.8), (5, 0.9))


@dataclass
class PriorCheck:
    rows: int = 0
    clients: int = 0
    new_rows: int = 0                     # 그 수임처 이력에 한 번만 나온 가맹점(처음 보는 곳 근사)
    unresolved_rows: int = 0
    lines: list[tuple] = field(default_factory=list)

    def render(self, current: dict) -> str:
        out = [
            "사무실 전체 이력 추천 — 한 수임처씩 빼고 나머지 수임처 이력으로 맞혀 보기",
            (f"처리된 행 {self.rows}건(대행사·업종 빈칸 제외) · 수임처 {self.clients}곳 · "
             f"그중 처음 보는 가맹점 {self.new_rows}건 · 지금 미해소 {self.unresolved_rows}건"),
            "",
            "기준(수임처≥ · 비중≥)  | 전체: 채움 · 정답      | 처음 보는 가맹점: 채움 · 정답 | 미해소 채움",
        ]
        for (k, s), allc, alla, newc, newa, unres in self.lines:
            mark = " ←지금" if (k, s) == (current["min_clients"], current["min_share"]) else ""
            out.append(f"  {k}곳 · {s:.0%}            | {allc:>6} · {alla:>6}      | "
                       f"{newc:>6} · {newa:>6}               | {unres:>5}건{mark}")
        out += ["",
                "비교: kafa-ask score 의 '행 단위 · 적용되는 답' 정답률(같은 잣대).",
                ("정답률이 그보다 높고 채움이 의미 있는 기준을 골라 config/rules.yaml 의 "
                 "recommend.office_prior 에 넣는다.")]
        return "\n".join(out) + "\n"


def _pct(a: int, b: int) -> str:
    return f"{a / b:.0%}" if b else "-"


def check_prior(records, unresolved=(), *, config_dir: str | None = None,
                grid=GRID) -> PriorCheck:
    """records: [(client, 거래처, 사업자번호, 업태, 종목, 차변코드)] — 처리된 행.
    unresolved: [(client, 거래처, 업태, 종목)] — 지금 미해소인 행(채울 수 있는 건수만 센다).
    """
    from kafa.rules.agents import agent_of

    def _agent(v, u, j):
        return agent_of(v or "", 업태=u or "", 종목=j or "", config_dir=config_dir)

    prior = OfficePrior(config_dir=config_dir)
    rows = []
    vendor_count: Counter = Counter()
    for client, vendor, bizno, 업태, 종목, code in records:
        if _agent(vendor, 업태, 종목) or not industry_key(업태, 종목, config_dir=config_dir):
            continue
        prior.add(client, 업태, 종목, code)
        vkey = (client, (bizno or "").strip() or (vendor or "").strip())
        vendor_count[vkey] += 1
        rows.append((client, vkey, 업태, 종목, int(code)))
    unres = [(c, u, j) for c, v, u, j in unresolved
             if not _agent(v, u, j) and industry_key(u, j, config_dir=config_dir)]

    pc = PriorCheck(rows=len(rows), clients=len({r[0] for r in rows}),
                    new_rows=sum(1 for r in rows if vendor_count[r[1]] == 1),
                    unresolved_rows=len(unresolved))
    for k, s in grid:
        cache: dict = {}

        def hit(client, u, j, exclude, _k=k, _s=s, _cache=cache):
            key = (industry_key(u, j, config_dir=config_dir), exclude)
            if key not in _cache:
                _cache[key] = prior.suggest(u, j, min_clients=_k, min_share=_s,
                                            exclude_client=exclude)
            return _cache[key]

        a_cov = a_ok = n_cov = n_ok = 0
        for client, vkey, u, j, code in rows:
            h = hit(client, u, j, client)
            if h is None:
                continue
            a_cov += 1
            a_ok += h.code == code
            if vendor_count[vkey] == 1:
                n_cov += 1
                n_ok += h.code == code
        u_cov = sum(1 for c, u, j in unres if hit(c, u, j, None) is not None)
        pc.lines.append(((k, s), _pct(a_cov, len(rows)), _pct(a_ok, a_cov),
                         _pct(n_cov, pc.new_rows), _pct(n_ok, n_cov), u_cov))
    return pc
