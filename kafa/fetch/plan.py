"""수집 계획 — 무엇을(거래처×기간) 어디에 받을지. 브라우저 없이 순수 로직.

이 모듈은 화면 조작을 하지 않으므로 테스트가 쉽다. 실제 클릭은 wehago.py 가 맡는다.
이미 받아둔 파일은 건너뛴다(중단 후 재개 가능 = 멱등).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str) -> str:
    """파일/폴더 이름으로 쓸 수 있게 정리(경로 조작 문자 제거)."""
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip(". ")
    return cleaned or "unknown"


def months_between(start: str, end: str) -> list[str]:
    """'2026-01', '2026-03' → ['2026-01','2026-02','2026-03']. 역순이면 빈 목록."""
    def parse(s):
        y, m = str(s).split("-")[:2]
        return int(y), int(m)
    (sy, sm), (ey, em) = parse(start), parse(end)
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def recent_months(n: int, *, today: date | None = None) -> list[str]:
    """오늘 기준 최근 n개월(이번 달 포함) 목록."""
    d = today or date.today()
    y, m = d.year, d.month
    out = []
    for _ in range(max(0, n)):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


@dataclass(frozen=True)
class DownloadTask:
    client: str            # 수임처 이름
    period: str            # 'YYYY-MM' 또는 기수 라벨('2026')
    url: str = ""          # 있으면 화면 검색 없이 이 주소로 바로 이동(로그인 세션 필요)
    here: bool = False     # True 면 이동하지 않고 **사람이 열어 둔 화면 그대로** 받는다

    @property
    def filename(self) -> str:
        return f"{self.period}.xlsx"


@dataclass
class DownloadPlan:
    tasks: list[DownloadTask] = field(default_factory=list)
    skipped: list[DownloadTask] = field(default_factory=list)   # 이미 받아둔 것

    @property
    def total(self) -> int:
        return len(self.tasks) + len(self.skipped)


def target_path(inbox, task: DownloadTask) -> Path:
    """inbox/<거래처>/<기간>.xlsx — 파이프라인의 고객별 폴더 규칙과 동일."""
    return Path(inbox) / safe_name(task.client) / task.filename


def build_plan(inbox, clients: list[str], periods: list[str], *,
               archive=None, urls: dict | None = None) -> DownloadPlan:
    """받을 목록 생성. inbox 나 archive 에 이미 있으면 건너뛴다(재개 가능).

    archive: 파이프라인이 처리 후 원본을 옮겨두는 폴더(out/_archive). 여기에 있으면
    이미 수집·처리된 것이므로 다시 받지 않는다.
    urls: {수임처: 접속 URL} — 주면 화면 검색 대신 주소로 바로 이동한다.
    """
    urls = urls or {}
    plan = DownloadPlan()
    for c in clients:
        for p in periods:
            t = DownloadTask(c, p, urls.get(c, ""))
            done = target_path(inbox, t).exists()
            if not done and archive:
                done = (Path(archive) / safe_name(c) / t.filename).exists()
            (plan.skipped if done else plan.tasks).append(t)
    return plan
