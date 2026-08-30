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

# 기간을 어떻게 정하느냐에 따라 필요한 selector 가 다르다.
#   screen   — 화면에 이미 설정된 기간(기수 전체 = 1년치) 그대로 받는다. 달력 조작 없음.
#   calendar — 달력을 눌러 월 단위로 지정한다(클릭 순서 보정 필요).
# 실화면 기록(2026-08) 결과 기수 기본값이 '2026.01.01 ~ 2026.12.31' 이라, screen 모드면
# 달력을 건드리지 않고 1년치를 한 번에 받을 수 있다. 근거: docs/decisions.md
PERIOD_MODES = ("screen", "calendar")
_BASE_SELECTORS = ("search_button", "excel_download_button")
_SEARCH_SELECTORS = ("client_search_input", "client_result_item")
_CALENDAR_SELECTORS = ("period_from_input", "period_to_input")

# URL 로 바로 이동할 때는 거래처 검색·선택이 필요 없어 보정 부담이 준다.
REQUIRED_SELECTORS = _SEARCH_SELECTORS + _CALENDAR_SELECTORS + _BASE_SELECTORS
REQUIRED_SELECTORS_URL_MODE = _CALENDAR_SELECTORS + _BASE_SELECTORS


def required_selectors(cfg: dict, *, url_mode: bool = False) -> tuple[str, ...]:
    """이 설정에서 실제로 필요한 selector 이름들."""
    mode = str((cfg or {}).get("period_mode", "calendar")).strip().lower()
    names = _BASE_SELECTORS
    if mode != "screen":
        names = _CALENDAR_SELECTORS + names
    if not url_mode:
        names = _SEARCH_SELECTORS + names
    return names


class SessionExpired(RuntimeError):
    """로그인 세션이 끊김 — 사람이 다시 로그인해야 한다(자동 로그인 하지 않음)."""


class NoAppPage(RuntimeError):
    """위하고 화면이 있는 탭을 못 찾음 — 잘못된 탭을 조작하지 않으려고 멈춘다."""


class StepFailed(RuntimeError):
    """어느 단계·어느 selector 에서 막혔는지 밝혀 준다(그냥 TimeoutError 면 못 고친다)."""


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
    for k in required_selectors(cfg, url_mode=url_mode):
        v = sel.get(k)
        if v is None or is_todo(v) or not str(v).strip():
            out.append(k)
    return out


def _is_open(pg) -> bool:
    try:
        return not pg.is_closed()
    except Exception:  # noqa: BLE001 — is_closed 가 없는 구현(테스트 더블)
        return True


def _url_lower(pg) -> str:
    try:
        return (pg.url or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def pick_page(page, cfg: dict):
    """조작할 탭을 고른다.

    회계 모듈이 새 창으로 열리고 처음 탭이 닫히는 일이 있어, 붙잡아 둔 page 객체가
    죽는다(TargetClosedError). 매번 **지금 살아 있는 탭 중 위하고 화면**을 고른다.
    광고·추적용으로 열리는 탭은 제외한다.
    """
    from kafa.fetch.inspect import pages_of

    cfg = cfg or {}
    hint = str(cfg.get("page_url_hint") or "").strip().lower()
    ignore = [str(x).lower() for x in (cfg.get("ignore_url_parts") or [])]
    sel = cfg.get("selectors", {}) or {}
    search = sel.get("search_button")

    candidates = [pg for pg in pages_of(page) if _is_open(pg)]
    candidates = [pg for pg in candidates
                  if not any(bad in _url_lower(pg) for bad in ignore)]
    best, best_score = None, -1
    for pg in candidates:
        score = 0
        if hint and hint in _url_lower(pg):
            score += 2
        if search and not is_todo(search):
            try:
                if pg.query_selector(search):
                    score += 3
            except Exception:  # noqa: BLE001 — 로딩 중이면 못 볼 수 있다
                pass
        if score > best_score:
            best, best_score = pg, score
    if best is not None and best_score > 0:
        return best
    if _is_open(page):
        return page                      # 아직 이동 전이면 원래 탭 그대로
    if candidates:
        return candidates[-1]            # 마지막으로 열린 탭
    raise NoAppPage(
        "위하고 화면이 있는 탭을 찾지 못했습니다(원래 탭이 닫혔을 수 있습니다). "
        "신용카드 화면 탭을 열어 둔 채로 다시 실행해 주세요.")


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


def _step(what: str, selector: str, action):
    """한 동작을 실행하고, 실패하면 단계 이름과 selector 를 붙여 다시 던진다."""
    try:
        return action()
    except Exception as e:  # noqa: BLE001
        raise StepFailed(f"[{what}] selector={selector!r} → "
                         f"{type(e).__name__}: {e}") from e


def fetch_one(page, cfg: dict, task: DownloadTask, dest: Path,
              on_step=None, resolve=None) -> Path:
    """한 거래처·한 기간을 받아 dest 에 저장. 실패 시 어느 단계인지 밝혀 예외.

    resolve 를 주면 **단계마다 살아 있는 탭을 다시 고른다**. 위하고는 광고 탭이
    수시로 열리고 닫혀 붙잡아 둔 page 가 죽는 일이 있다(TargetClosedError).
    """
    sel = cfg["selectors"]
    say = on_step or (lambda _m: None)

    def P():
        if resolve is None:
            return page
        try:
            return resolve()
        except Exception:  # noqa: BLE001 — 못 고르면 원래 것으로
            return page

    page = P()
    try:
        say(f"대상 탭 {(page.url or '')[:70]}")
    except Exception:  # noqa: BLE001
        pass
    timeout = int(cfg.get("timeout_ms", 20000))
    fmt = cfg.get("period_format", "%Y-%m")

    # 1) 수임처로 이동 — here 면 사람이 열어 둔 화면 그대로, URL 이 있으면 주소로 바로,
    #    둘 다 아니면 화면에서 검색해 고른다.
    if task.here:
        say("이동 생략(열어 둔 화면 그대로)")
    elif task.url:
        say("주소로 이동")
        P().goto(task.url, timeout=timeout)
        marker = (cfg.get("selectors", {}) or {}).get("login_marker")
        if marker and P().query_selector(marker):
            raise SessionExpired("로그인 화면이 나타났습니다(세션 만료).")
    else:
        say("수임처 검색")
        _step("수임처 검색", sel["client_search_input"],
              lambda: page.fill(sel["client_search_input"], task.client,
                                timeout=timeout))
        item = sel["client_result_item"].replace("{client}", task.client)
        _step("수임처 선택", item, lambda: page.click(item, timeout=timeout))

    # 2) 구분(매입/매출) — 화면에 선택 목록이 있으면 매입으로 맞춘다
    _select_kind(P(), cfg, timeout, say)

    # 3) 기간 설정
    if str(cfg.get("period_mode", "calendar")).strip().lower() != "screen":
        p = format_period(task.period, fmt)
        say(f"기간 입력 {p}")
        _step("기간 시작", sel["period_from_input"],
              lambda: page.fill(sel["period_from_input"], p, timeout=timeout))
        _step("기간 종료", sel["period_to_input"],
              lambda: page.fill(sel["period_to_input"], p, timeout=timeout))
    # screen 모드면 화면에 이미 잡혀 있는 기간(기수 전체)을 그대로 쓴다.

    # 4) 조회
    say("조회")
    _step("조회", sel["search_button"],
          lambda: P().click(sel["search_button"], timeout=timeout))

    # 5) 엑셀 다운로드 → 지정 경로에 저장
    say("엑셀 변환·다운로드")
    dest.parent.mkdir(parents=True, exist_ok=True)

    expect = str(cfg.get("expect_filename_contains", "")).strip()

    def _download():
        pg = P()
        with pg.expect_download(timeout=timeout) as dl:
            pg.click(sel["excel_download_button"], timeout=timeout)
        name = getattr(dl.value, "suggested_filename", "") or ""
        # 구분을 못 맞췄을 수 있으니 **받은 파일 이름**으로 매입 자료인지 확인한다.
        if expect and name and expect not in name:
            raise StepFailed(
                f"받은 파일이 '{expect}' 자료가 아닙니다: {name!r}. "
                f"화면의 구분을 '{cfg.get('kind_value')}' 으로 맞춘 뒤 다시 실행하세요.")
        dl.value.save_as(str(dest))

    _step("엑셀 다운로드", sel["excel_download_button"], _download)

    # 6) 변환 완료 알림이 뜨면 닫는다(안 닫으면 다음 건이 가려진다)
    confirm = sel.get("download_confirm")
    if confirm and not is_todo(confirm):
        try:
            P().click(confirm, timeout=int(cfg.get("confirm_timeout_ms", 5000)))
        except Exception:  # noqa: BLE001 — 알림이 없을 수도 있다
            pass
    return dest


def _as_list(value) -> list[str]:
    """selector 는 하나 또는 후보 여러 개(먼저 눌리는 것 사용)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v and not is_todo(v)]
    return [] if is_todo(value) else [str(value)]


def _select_kind(page, cfg: dict, timeout: int, say=None) -> None:
    """조회 구분을 '매입' 으로 맞춘다.

    화면에 비슷한 드롭다운이 여럿이라 selector 하나로 콕 집기 어렵다. 그래서
    ① 이미 매입이면 건드리지 않고 ② 아니면 후보를 차례로 시도하고 ③ 그래도 안 되면
    막지 않고 넘어간다 — 잘못된 자료를 받는 사고는 **파일 이름 검증**이 막는다.
    """
    say = say or (lambda _m: None)
    sel = cfg.get("selectors", {}) or {}
    kind = str(cfg.get("kind_value", "")).strip()
    option = sel.get("kind_option")
    openers = _as_list(sel.get("kind_select_open"))
    if not kind or not option or is_todo(option) or not openers:
        say("구분 선택 생략(설정 없음)")
        return

    current = sel.get("kind_current")
    if current and not is_todo(current):
        try:
            if page.query_selector(current.replace("{kind}", kind)):
                say(f"구분 확인 — 이미 '{kind}'")
                return
        except Exception:  # noqa: BLE001 — 확인 실패는 치명적이지 않다
            pass

    if not cfg.get("kind_autoselect", False):
        say(f"구분이 '{kind}' 인지 확인해 주세요(자동 선택 꺼짐) — 받은 파일 이름으로 검증합니다")
        return

    target = option.replace("{kind}", kind)
    tries = int(cfg.get("kind_try_timeout_ms", 4000))
    for cand in openers:
        try:
            page.click(cand, timeout=tries)
            page.click(target, timeout=tries)
            say(f"구분 선택 '{kind}'")
            return
        except Exception:  # noqa: BLE001 — 다음 후보로
            continue
    say(f"구분을 자동으로 못 맞췄습니다 — 받은 파일 이름으로 확인합니다")


def run_fetch(page, plan: DownloadPlan, inbox, *, cfg: Optional[dict] = None,
              sleep: Callable[[float], None] = None,
              on_progress: Optional[Callable[[DownloadTask, str], None]] = None,
              on_session_expired: Optional[Callable[[], None]] = None,
              on_step: Optional[Callable[[str], None]] = None,
              on_failure: Optional[Callable[[DownloadTask, Exception], None]] = None
              ) -> FetchResult:
    """계획대로 순회 수집. 한 건 실패해도 계속 진행한다.

    on_session_expired: 세션이 끊겼을 때 호출(사람에게 재로그인 요청). 지정하면 그 건을
    한 번 다시 시도한다. 자동 로그인은 하지 않는다.
    """
    import time

    cfg = cfg or load_fetch_config()
    # here(열어 둔 화면 그대로) 도 화면 검색이 필요 없다 — URL 모드와 같게 본다.
    url_mode = bool(plan.tasks) and all(t.url or t.here for t in plan.tasks)
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
                dest = fetch_one(pick_page(page, cfg), cfg, task,
                                 target_path(inbox, task), on_step=on_step,
                                 resolve=lambda: pick_page(page, cfg))
            except SessionExpired:
                if on_session_expired is None:
                    raise
                on_session_expired()          # 사람이 다시 로그인
                dest = fetch_one(pick_page(page, cfg), cfg, task,
                                 target_path(inbox, task), on_step=on_step,
                                 resolve=lambda: pick_page(page, cfg))
            res.saved.append(dest)
            if on_progress:
                on_progress(task, "저장")
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않음
            res.failures[label] = f"{type(e).__name__}: {e}"
            if on_progress:
                on_progress(task, f"실패({type(e).__name__})")
            if on_failure:
                on_failure(task, e)
        if i < len(plan.tasks) - 1:
            sleep(delay)          # 서버 부담 완화
    return res
