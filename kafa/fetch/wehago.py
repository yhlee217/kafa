"""위하고 화면 조작 — 거래처 순회 → 조회 → 엑셀 다운로드 → inbox 저장.

selector 는 config/fetch/wehago.yaml 에서 읽는다(추측 금지 — 보정 전이면 실행을 막는다).
브라우저 조작은 얇게 두고, 무엇을 받을지(계획)·어디에 둘지(경로)는 plan.py 가 맡는다.

한 건 실패해도 다음 거래처로 계속 진행하고, 실패 목록을 돌려준다(부분 성공 허용).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from kafa.config_loader import is_todo
from kafa.fetch.plan import DownloadPlan, DownloadTask, target_path

_DEFAULT_CFG = Path(__file__).resolve().parent.parent.parent / "config" / "fetch" / "wehago.yaml"

# URL 로 바로 이동할 때는 거래처 검색·선택이 필요 없어 보정 부담이 준다.
REQUIRED_SELECTORS = ("client_search_input", "client_result_item",
                      "period_from_input", "period_to_input",
                      "search_button", "excel_download_button")
REQUIRED_SELECTORS_URL_MODE = ("period_from_input", "period_to_input",
                               "search_button", "excel_download_button")


class SessionExpired(RuntimeError):
    """로그인 세션이 끊김 — 사람이 다시 로그인해야 한다(자동 로그인 하지 않음)."""


class NotCalibrated(RuntimeError):
    """selector 가 아직 보정되지 않음 — 추측으로 클릭하지 않기 위해 실행을 막는다."""


def load_fetch_config(path=None) -> dict:
    p = Path(path) if path else _DEFAULT_CFG
    with p.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg


def missing_selectors(cfg: dict, *, url_mode: bool = False) -> list[str]:
    """아직 TODO/빈값인 필수 selector 목록. url_mode 면 검색 관련은 제외."""
    sel = cfg.get("selectors", {}) or {}
    out = []
    for k in (REQUIRED_SELECTORS_URL_MODE if url_mode else REQUIRED_SELECTORS):
        v = sel.get(k)
        if v is None or is_todo(v) or not str(v).strip():
            out.append(k)
    return out


def format_period(period: str, fmt: str) -> str:
    """'2026-03' → 화면 입력 형식. %Y/%m 만 치환한다(날짜 파싱 불필요)."""
    y, m = period.split("-")[:2]
    return fmt.replace("%Y", y).replace("%m", m)


@dataclass
class FetchResult:
    saved: list[Path] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)   # "고객/기간" → 사유
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


def fetch_one(page, cfg: dict, task: DownloadTask, dest: Path) -> Path:
    """한 거래처·한 기간을 받아 dest 에 저장. 실패 시 예외."""
    sel = cfg["selectors"]
    timeout = int(cfg.get("timeout_ms", 20000))
    fmt = cfg.get("period_format", "%Y-%m")

    # 1) 수임처로 이동 — URL 이 있으면 주소로 바로(로그인 세션 필요), 없으면 화면 검색
    if task.url:
        page.goto(task.url, timeout=timeout)
        marker = (cfg.get("selectors", {}) or {}).get("login_marker")
        if marker and page.query_selector(marker):
            raise SessionExpired("로그인 화면이 나타났습니다(세션 만료).")
    else:
        page.fill(sel["client_search_input"], task.client, timeout=timeout)
        page.click(sel["client_result_item"].replace("{client}", task.client),
                   timeout=timeout)

    # 2) 기간 설정 후 조회
    p = format_period(task.period, fmt)
    page.fill(sel["period_from_input"], p, timeout=timeout)
    page.fill(sel["period_to_input"], p, timeout=timeout)
    page.click(sel["search_button"], timeout=timeout)

    # 3) 엑셀 다운로드 → 지정 경로에 저장
    dest.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=timeout) as dl:
        page.click(sel["excel_download_button"], timeout=timeout)
    dl.value.save_as(str(dest))
    return dest


def run_fetch(page, plan: DownloadPlan, inbox, *, cfg: Optional[dict] = None,
              sleep: Callable[[float], None] = None,
              on_progress: Optional[Callable[[DownloadTask, str], None]] = None,
              on_session_expired: Optional[Callable[[], None]] = None
              ) -> FetchResult:
    """계획대로 순회 수집. 한 건 실패해도 계속 진행한다.

    on_session_expired: 세션이 끊겼을 때 호출(사람에게 재로그인 요청). 지정하면 그 건을
    한 번 다시 시도한다. 자동 로그인은 하지 않는다.
    """
    import time

    cfg = cfg or load_fetch_config()
    url_mode = all(t.url for t in plan.tasks) and bool(plan.tasks)
    miss = missing_selectors(cfg, url_mode=url_mode)
    if miss:
        raise NotCalibrated(
            "화면 selector 가 아직 보정되지 않았습니다: " + ", ".join(miss)
            + "\nconfig/fetch/wehago.yaml 을 실제 화면에 맞춰 채운 뒤 다시 실행하세요.")

    sleep = sleep or time.sleep
    delay = float(cfg.get("delay_seconds", 3.0))
    res = FetchResult(skipped=len(plan.skipped))

    for i, task in enumerate(plan.tasks):
        label = f"{task.client}/{task.period}"
        try:
            try:
                dest = fetch_one(page, cfg, task, target_path(inbox, task))
            except SessionExpired:
                if on_session_expired is None:
                    raise
                on_session_expired()          # 사람이 다시 로그인
                dest = fetch_one(page, cfg, task, target_path(inbox, task))
            res.saved.append(dest)
            if on_progress:
                on_progress(task, "저장")
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않음
            res.failures[label] = f"{type(e).__name__}: {e}"
            if on_progress:
                on_progress(task, f"실패({type(e).__name__})")
        if i < len(plan.tasks) - 1:
            sleep(delay)          # 서버 부담 완화
    return res
