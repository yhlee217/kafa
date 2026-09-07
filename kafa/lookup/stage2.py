"""2단계 — 카드사 내역으로도 안 풀린 건을 어디서 찾을지 정한다.

1단계(카드사 이용내역)에서 실가맹점이 나오면 끝이다. 안 나온 건(`여전히대행사`)은
승인번호는 얻었으니, 그 승인번호로 **PG 소비자 조회 페이지**에 물어볼 수 있다.

PG 조회 API 는 쓸 수 없다 — 가맹점용이라 우리(구매자)에게는 안 열린다.
근거와 경로는 `config/lookup/pg_sites.yaml`.

건별 조회라 전부 할 수 없다. 실측상 **금액 상위 5%(57건)가 금액의 71%** 이므로
금액 큰 것부터 자른다.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from kafa.config_loader import load_pg_sites
from kafa.lookup.merge import FOUND
from kafa.lookup.statements import normalize_amount
from kafa.rules.agents import normalize

PG_PAGE = "pg_page"
APP_ORDER = "app_order"
VAN_STATEMENT = "van_statement"
GIRO = "giro"
UNKNOWN = "경로없음"


@dataclass
class Task:
    """2단계 조회 한 건."""
    경로: str = UNKNOWN
    창구: str = ""
    주소: str = ""
    입력: str = ""
    관문: str = ""
    번호: str = ""
    수임처: str = ""
    거래일자: str = ""
    금액: Decimal = Decimal(0)
    대행사: str = ""
    승인번호: str = ""


def route_for(대행사: str, *, config_dir: str | None = None) -> tuple[str, dict]:
    """이 대행사는 어디서 찾나 — (경로, 창구 정보)."""
    spec = load_pg_sites(config_dir) or {}
    routes = spec.get("routes") or {}
    key = normalize(대행사 or "")
    if not key:
        return UNKNOWN, {}

    for name, site in (routes.get(PG_PAGE, {}).get("sites") or {}).items():
        if normalize(name) in key:
            return PG_PAGE, {"창구": name, **site}
    for route in (APP_ORDER, VAN_STATEMENT, GIRO):
        spec_route = routes.get(route) or {}
        for name in (spec_route.get("대행사") or []):
            if normalize(str(name)) in key:
                return route, {"창구": str(name),
                               "관문": spec_route.get("관문") or [],
                               "주소": "", "입력": [],
                               "조회제외": bool(spec_route.get("조회제외"))}
    return UNKNOWN, {}


def build_tasks(resolved_csv: str | Path, out_csv: str | Path, *,
                min_amount: Decimal | int | None = None,
                top_n: int | None = None,
                config_dir: str | None = None) -> list[Task]:
    """1단계 결과에서 2단계 조회 목록을 만든다. 금액 큰 것부터."""
    spec = load_pg_sites(config_dir) or {}
    priority = spec.get("priority") or {}
    floor = Decimal(str(priority.get("min_amount", 0) if min_amount is None else min_amount))
    skip_groups = set(spec.get("skip_groups") or [])
    limit = int(priority.get("top_n", 0) if top_n is None else top_n)

    tasks: list[Task] = []
    with Path(resolved_csv).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("결과") == FOUND:
                continue                       # 1단계에서 이미 풀렸다
            if row.get("갈래", "") in skip_groups:
                continue                       # 이 갈래는 조회할 실가맹점이 없다
            금액 = normalize_amount(row.get("합계", ""))
            if abs(금액) < floor:
                continue                       # 작은 건은 들일 품이 아깝다
            대행사 = row.get("대행사", "")
            경로, site = route_for(대행사, config_dir=config_dir)
            if site.get("조회제외"):
                continue                       # 지로 등 — 고지서로 확인할 것
            tasks.append(Task(
                경로=경로, 창구=site.get("창구", ""), 주소=site.get("주소", ""),
                입력=", ".join(str(x) for x in (site.get("입력") or [])),
                관문=", ".join(str(x) for x in (site.get("관문") or [])),
                번호=row.get("번호", ""), 수임처=row.get("수임처", ""),
                거래일자=row.get("거래일자", ""), 금액=금액, 대행사=대행사,
                승인번호=row.get("승인번호", "")))

    tasks.sort(key=lambda t: -abs(t.금액))
    if limit > 0:
        tasks = tasks[:limit]

    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["경로", "창구", "주소", "입력", "관문", "번호", "수임처",
                    "거래일자", "금액", "대행사", "승인번호", "찾은가맹점", "품명"])
        for t in tasks:
            w.writerow([t.경로, t.창구, t.주소, t.입력, t.관문, t.번호, t.수임처,
                        t.거래일자, t.금액, t.대행사, t.승인번호, "", ""])
    return tasks


def summarize(tasks: list[Task]) -> str:
    """경로별 몇 건인지 — 어디부터 손댈지 정하는 숫자."""
    by: dict[str, list[Task]] = {}
    for t in tasks:
        by.setdefault(t.경로, []).append(t)
    lines = []
    for route in (PG_PAGE, APP_ORDER, VAN_STATEMENT, GIRO, UNKNOWN):
        got = by.get(route)
        if not got:
            continue
        총액 = sum(abs(t.금액) for t in got)
        lines.append(f"  {route:14} {len(got):>4}건  {총액:>12,}원")
        창구 = {}
        for t in got:
            창구[t.창구 or "-"] = 창구.get(t.창구 or "-", 0) + 1
        for name, n in sorted(창구.items(), key=lambda kv: -kv[1])[:5]:
            lines.append(f"      {name:20} {n:>4}건")
    return "\n".join(lines)
