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


class WrongClient(RuntimeError):
    """화면이 다른 수임처다 — 그대로 받으면 남의 자료를 이 이름으로 저장하게 된다."""


class NoData(RuntimeError):
    """조회 결과가 없다 — 실패가 아니라 '받을 게 없음'으로 센다."""


class NotReady(RuntimeError):
    """화면이 제 시간 안에 준비되지 않았다(로딩이 느린 수임처)."""


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
        if not _as_list(sel.get(k)):
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


def _first_selector(value) -> str:
    cands = _as_list(value)
    return cands[0] if cands else ""


def pick_page(page, cfg: dict, *, want: str = "ledger"):
    """조작할 탭을 고른다(want='ledger' 전표화면 / 'dashboard' 수임처 목록).

    회계 모듈이 새 창으로 열리고 처음 탭이 닫히는 일이 있어, 붙잡아 둔 page 객체가
    죽는다(TargetClosedError). 매번 **지금 살아 있는 탭 중 맞는 화면**을 고른다.
    주소를 about:blank 로 보고하는 탭이 있어 **요소 존재 여부**를 더 크게 본다.
    """
    from kafa.fetch.inspect import pages_of

    cfg = cfg or {}
    sel = cfg.get("selectors", {}) or {}
    ignore = [str(x).lower() for x in (cfg.get("ignore_url_parts") or [])
              if str(x).lower() != "about:blank"]
    if want == "dashboard":
        hint = str(cfg.get("dashboard_url_hint") or "").strip().lower()
        marker = _first_selector(sel.get("client_search_input"))
    else:
        hint = str(cfg.get("page_url_hint") or "").strip().lower()
        marker = _first_selector(sel.get("search_button"))

    candidates = [pg for pg in pages_of(page) if _is_open(pg)]
    candidates = [pg for pg in candidates
                  if not any(bad in _url_lower(pg) for bad in ignore)]
    best, best_score = None, -1
    for pg in candidates:
        score = 0
        if hint and hint in _url_lower(pg):
            score += 2
        if marker:
            try:
                if pg.query_selector(marker):
                    score += 3
            except Exception:  # noqa: BLE001 — 로딩 중이면 못 볼 수 있다
                pass
        if score > best_score:
            best, best_score = pg, score
    if best is not None and best_score > 0:
        return best
    if want == "dashboard":
        raise NoAppPage("수임처 목록 화면을 찾지 못했습니다. "
                        "위하고 메인(수임처 목록) 탭을 열어 두세요.")
    if _is_open(page):
        return page                      # 아직 이동 전이면 원래 탭 그대로
    if candidates:
        return candidates[-1]            # 마지막으로 열린 탭
    # 쓸 탭이 하나도 없으면 새로 연다(로그인 세션은 브라우저에 남아 있다).
    try:
        return page.context.new_page()
    except Exception:  # noqa: BLE001
        pass
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
    empty: list[str] = field(default_factory=list)           # 조회 결과가 없던 곳
    retried: int = 0                                         # 다시 시도한 횟수
    probed: dict = field(default_factory=dict)               # 점검 모드: 라벨 → 결과

    @property
    def ok(self) -> bool:
        return not self.failures


def _as_list(value) -> list[str]:
    """selector 는 하나 또는 후보 여러 개(먼저 눌리는 것 사용)."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(v).strip() for v in values
            if v is not None and str(v).strip() and not is_todo(v)]


def _click_any(page, selectors, timeout: int, what: str, *, button: str = "left"):
    """후보 selector 를 차례로 눌러 본다. 하나라도 되면 그 selector 를 돌려준다.

    화면에 비슷한 요소가 많거나 위하고가 화면 구조를 조금 바꿔도 견디게 하기 위함.
    마지막 후보까지 실패하면 시도한 목록을 붙여 예외를 던진다.
    """
    cands = _as_list(selectors)
    if not cands:
        raise StepFailed(f"[{what}] 누를 selector 가 설정되지 않았습니다")
    errors = []
    for i, cand in enumerate(cands):
        # 마지막 후보에는 남은 시간을 다 준다(앞 후보는 짧게 훑고 넘어간다).
        t = timeout if i == len(cands) - 1 else min(timeout, 5000)
        try:
            if button == "left":
                page.click(cand, timeout=t)
            else:
                page.click(cand, timeout=t, button=button)
            return cand
        except Exception as e:  # noqa: BLE001 — 다음 후보로
            errors.append(f"{cand} → {type(e).__name__}")
    raise StepFailed(f"[{what}] 후보를 모두 시도했지만 못 눌렀습니다: "
                     + " | ".join(errors))


def _click_any_right(page, selectors, timeout: int, what: str):
    """오른쪽 클릭으로 컨텍스트 메뉴를 연다(후보를 차례로 시도)."""
    return _click_any(page, selectors, timeout, what, button="right")


def _wait_ready(get_page, selectors, timeout_ms: int, what: str, *,
                sleep=None, say=None) -> bool:
    """요소가 나타날 때까지 기다린다(느린 수임처 대비).

    페이지 자체가 바뀔 수 있어 매번 다시 고른다. 못 기다리면 NotReady.
    """
    import time as _time

    sleep = sleep or _time.sleep
    say = say or (lambda _m: None)
    cands = _as_list(selectors)
    if not cands:
        return False
    deadline = _time.monotonic() + timeout_ms / 1000.0
    waited = False
    while True:
        pg = get_page()
        for cand in cands:
            try:
                if pg.query_selector(cand):
                    if waited:
                        say(f"{what} 준비됨")
                    return True
            except Exception:  # noqa: BLE001 — 로딩 중이면 못 볼 수 있다
                pass
        if _time.monotonic() >= deadline:
            raise NotReady(f"[{what}] {int(timeout_ms / 1000)}초 안에 화면이 "
                           "준비되지 않았습니다")
        if not waited:
            say(f"{what} 기다리는 중…")
            waited = True
        sleep(0.5)


# 화면에 **보이는 알림창**을 찾아 그 글자를 본다.
# text="..." 완전일치는 마침표 하나만 달라도 안 걸려서 놓쳤다(실측 2026-09-02).
_DIALOG_JS = r"""
([selectors, words]) => {
  // 띄어쓰기·줄바꿈을 모두 지우고 비교한다.
  // 실제 문구가 '조회 조건에 맞는 데이터가 없습니다.' 처럼 띄어쓰기가 들어가 있어
  // 조각을 그대로 비교하면 놓친다(실측 2026-09-02).
  const squash = (x) => (x || '').replace(/\s+/g, '');
  const keys = words.map(squash).filter(Boolean);

  // 크기만 보면 안 된다 — visibility:hidden 인 요소도 자리를 차지한다.
  // 숨어 있는 안내문을 팝업으로 오인해 전부 '자료 없음' 이 됐다(실측 2026-09-02).
  const visible = (el) => {
    try {
      if (el.checkVisibility) {
        return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
      }
    } catch (e) {}
    let r;
    try { r = el.getBoundingClientRect(); } catch (e) { return false; }
    if (r.width <= 0 || r.height <= 0) return false;
    let cur = el, depth = 0;
    while (cur && depth < 12) {
      const st = getComputedStyle(cur);
      if (st.display === 'none' || st.visibility === 'hidden') return false;
      if (parseFloat(st.opacity || '1') <= 0.05) return false;
      cur = cur.parentElement; depth++;
    }
    return true;
  };

  for (const sel of selectors) {
    let els = [];
    try { els = document.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of els) {
      if (!visible(el)) continue;
      const raw = (el.textContent || '').replace(/\s+/g, ' ').trim();
      const t = squash(raw);
      for (const k of keys) {
        if (t.includes(k)) return raw.slice(0, 120);
      }
    }
  }
  return '';
}
"""


def _has_any_text(pg, texts, cfg: dict | None = None) -> str:
    """조회 결과가 없다는 알림이 떠 있으면 그 글자를 돌려준다.

    ① 보이는 알림창을 찾아 문구가 들어 있는지 본다(마침표·줄바꿈 차이에 강하다).
    ② 그래도 못 찾으면 부분일치 text 선택자로 한 번 더 본다.
    """
    words = [str(t) for t in (texts or []) if t]
    if not words:
        return ""
    sels = list(((cfg or {}).get("empty_dialog_selectors")) or
                [".WSC_LUXConfirm", ".dialog_alert", ".dialog_wrap",
                 ".WSC_LUXDraggableDialog", "[role=dialog]", ".dialog_content"])
    try:
        found = pg.evaluate(_DIALOG_JS, [sels, words])
        if found:
            return str(found)
    except Exception:  # noqa: BLE001 — 아래 방식으로 한 번 더
        pass
    for t in words:
        for sel in (f"text={t}:visible", f'text="{t}":visible'):
            try:
                if pg.query_selector(sel):
                    return t
            except Exception:  # noqa: BLE001
                continue
    return ""


def _watch_for_empty(get_page, cfg: dict, sleep, baseline: str = "") -> str:
    """조회 직후 '자료 없음' 팝업이 뜨는지 잠깐 지켜본다. 뜨면 그 문구를 돌려준다.

    baseline: 조회 **전**에 이미 보이던 같은 문구. 화면에 상시 떠 있는 안내문을
    팝업으로 오인하지 않도록, 조회 전과 달라졌을 때만 '자료 없음' 으로 본다.
    """
    import time as _time

    texts = cfg.get("empty_result_texts") or []
    wait = float(cfg.get("empty_wait_seconds",
                         cfg.get("after_search_seconds", 1.5)))
    if not texts:
        sleep(wait)
        return ""
    deadline = _time.monotonic() + wait
    while True:
        found = _has_any_text(get_page(), texts, cfg)
        if found and found != baseline:
            return found
        if _time.monotonic() >= deadline:
            return ""
        sleep(0.3)


def _dismiss_popup(page, cfg: dict) -> None:
    """알림 팝업을 닫는다. 안 닫으면 다음 수임처 화면이 가려진다."""
    for cand in _as_list((cfg.get("selectors", {}) or {}).get("popup_confirm")):
        try:
            page.click(cand, timeout=int(cfg.get("confirm_timeout_ms", 5000)))
            return
        except Exception:  # noqa: BLE001 — 다음 후보로
            continue


def _step(what: str, selector: str, action):
    """한 동작을 실행하고, 실패하면 단계 이름과 selector 를 붙여 다시 던진다."""
    try:
        return action()
    except Exception as e:  # noqa: BLE001
        raise StepFailed(f"[{what}] selector={selector!r} → "
                         f"{type(e).__name__}: {e}") from e


def fetch_one(page, cfg: dict, task: DownloadTask, dest: Path,
              on_step=None, resolve=None, sleep=None, download: bool = True,
              on_capture=None):
    """한 거래처·한 기간을 받아 dest 에 저장. 실패 시 어느 단계인지 밝혀 예외.

    resolve 를 주면 **단계마다 살아 있는 탭을 다시 고른다**. 위하고는 광고 탭이
    수시로 열리고 닫혀 붙잡아 둔 page 가 죽는 일이 있다(TargetClosedError).
    """
    import time as _time

    say = on_step or (lambda _m: None)
    sleep = sleep or _time.sleep

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

    try:
        return _fetch_steps(P, cfg, task, dest, say, sleep, download, on_capture)
    except NoData:
        raise                       # 자료없음은 이미 그 화면을 찍었다
    except Exception as e:  # noqa: BLE001 — 막힌 그 순간의 화면을 남기고 다시 던진다
        if on_capture:
            try:
                on_capture(P(), _failure_kind(e))
            except Exception:  # noqa: BLE001 — 사진 실패가 원인을 가리지 않게
                pass
        raise


def _fetch_steps(P, cfg: dict, task: DownloadTask, dest: Path, say, sleep,
                 download: bool, on_capture):
    """실제 단계들. 예외는 위에서 잡아 화면을 남긴 뒤 다시 던진다."""
    sel = cfg["selectors"]
    timeout = int(cfg.get("timeout_ms", 20000))
    fmt = cfg.get("period_format", "%Y-%m")

    # 1) 수임처로 이동 — here 면 사람이 열어 둔 화면 그대로, URL 이 있으면 주소로 바로,
    #    둘 다 아니면 화면에서 검색해 고른다.
    if task.here:
        say("이동 생략(열어 둔 화면 그대로)")
    elif task.url:
        say("주소로 이동")
        _goto(P(), task.url, timeout, cfg)
        marker = (cfg.get("selectors", {}) or {}).get("login_marker")
        if marker and P().query_selector(marker):
            raise SessionExpired("로그인 화면이 나타났습니다(세션 만료).")
    else:
        _open_client(P, cfg, task, timeout, say)

    # 2) 신용카드 조회 화면이 뜰 때까지. 회계 첫 화면이 뜨면 '신용카드' 를 눌러 들어간다.
    if not task.here:
        try:
            _ensure_ledger_screen(P, cfg, sleep, say)
        except NotReady:
            # 주소가 안 통하면(화면 개편·권한 등) 목록에서 여는 길로 되돌아간다.
            if not task.url or not (task.cno or task.client):
                raise
            say("주소로 못 들어갔습니다 — 수임처 목록에서 여는 길로 바꿉니다")
            _open_client(P, cfg, task, timeout, say)
            _ensure_ledger_screen(P, cfg, sleep, say)

    # 2-1) **그 수임처 화면이 맞는지** 확인. 전환이 안 됐으면 목록으로 다시 연다.
    if not task.here:
        try:
            _verify_client(P, cfg, task, sleep, say)
        except WrongClient:
            if not task.url or not (task.cno or task.client):
                raise
            say("다른 수임처 화면입니다 — 목록에서 다시 엽니다")
            _open_client(P, cfg, task, timeout, say)
            _ensure_ledger_screen(P, cfg, sleep, say)
            _verify_client(P, cfg, task, sleep, say)
        say(f"수임처 확인됨")

    # 3) 구분(매입/매출) — 화면에 선택 목록이 있으면 매입으로 맞춘다
    _select_kind(P(), cfg, timeout, say)

    # 3) 기간 설정
    if str(cfg.get("period_mode", "calendar")).strip().lower() != "screen":
        p = format_period(task.period, fmt)
        say(f"기간 입력 {p}")
        _step("기간 시작", sel["period_from_input"],
              lambda: P().fill(sel["period_from_input"], p, timeout=timeout))
        _step("기간 종료", sel["period_to_input"],
              lambda: P().fill(sel["period_to_input"], p, timeout=timeout))
    # screen 모드면 화면에 이미 잡혀 있는 기간(기수 전체)을 그대로 쓴다.

    # 5) 조회 — 누르기 전에 같은 문구가 이미 떠 있는지 봐 둔다(상시 안내문 구분용)
    before = _has_any_text(P(), cfg.get("empty_result_texts") or [], cfg)
    say("조회")
    _click_any(P(), sel["search_button"], timeout, "조회")

    # 결과가 없으면 팝업이 뜬다. 팝업이 나타날 시간을 주고, 뜨면 닫고 넘어간다.
    empty = _watch_for_empty(P, cfg, sleep, baseline=before)
    if empty:
        say(f"조회 결과 없음 — {empty}")
        if on_capture:
            on_capture(P(), "자료없음")      # 팝업이 뜬 화면을 그대로 남긴다
        _dismiss_popup(P(), cfg)
        raise NoData(empty)

    if not download:
        # 점검 모드 — 여기까지 왔으면 받을 자료가 있다는 뜻. 다운로드는 하지 않는다.
        say("자료 있음(점검 모드라 받지 않음)")
        if on_capture:
            on_capture(P(), "자료있음")
        _close_ledger(P, cfg, task, say)
        return None

    # 6) 엑셀 다운로드 → 지정 경로에 저장
    say("엑셀 변환·다운로드")
    dest.parent.mkdir(parents=True, exist_ok=True)

    expect = str(cfg.get("expect_filename_contains", "")).strip()

    # '엑셀변환' 은 표의 **데이터 행에서 우클릭**해야 나오는 메뉴 안에 있다.
    # (담당자 확인 2026-08-30 — docs/domain_notes.md)
    ctx_target = _as_list(sel.get("excel_context_target"))

    tries = max(1, int(cfg.get("menu_retries", 3)))

    def _open_menu_and_pick(pg):
        """우클릭 → 메뉴에서 '엑셀변환'. 표가 아직 안 그려졌으면 다시 시도한다."""
        last = None
        for i in range(tries):
            try:
                if ctx_target:
                    _click_any_right(pg, ctx_target, timeout, "엑셀 메뉴 열기")
                _click_any(pg, sel["excel_download_button"],
                           timeout if i == tries - 1 else 5000, "엑셀 다운로드")
                return
            except Exception as e:  # noqa: BLE001 — 표가 늦게 뜨는 경우
                last = e
                say(f"엑셀 메뉴가 아직 안 떠서 다시 시도합니다 ({i + 1}/{tries})")
                sleep(float(cfg.get("menu_retry_seconds", 2.0)))
        raise last

    def _download():
        pg = P()
        if ctx_target:
            _wait_ready(P, ctx_target, int(cfg.get("ready_timeout_ms", 30000)),
                        "조회 결과 표", sleep=sleep, say=say)
        with pg.expect_download(timeout=timeout) as dl:
            _open_menu_and_pick(pg)
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

    # 7) 여러 수임처를 도는 중이면 회계 탭을 닫는다(탭이 쌓이면 다음 건이 헷갈린다)
    if on_capture:
        on_capture(P(), "저장")
    _close_ledger(P, cfg, task, say)
    return dest


_COMPANY_NOISE = ("(주)", "㈜", "주식회사", "(유)", "유한회사", "(합)", "(재)", "(사)",
                  "주식", "회사")


def normalize_company(name: str) -> str:
    """회사명 비교용 정규화 — 띄어쓰기·법인 표기 차이를 무시한다."""
    t = (name or "").strip()
    for w in _COMPANY_NOISE:
        t = t.replace(w, "")
    return "".join(t.split()).lower()


def same_client(expected: str, on_screen: str) -> bool:
    """화면에 뜬 이름이 받으려는 수임처인가(한쪽이 다른 쪽을 포함하면 같다고 본다)."""
    a, b = normalize_company(expected), normalize_company(on_screen)
    if not a or not b:
        return False
    return a in b or b in a


def _screen_client_name(pg, cfg: dict) -> str:
    """화면이 보여주는 회사명. 지정한 곳이 없으면 탭 제목을 쓴다."""
    sel = (cfg.get("selectors", {}) or {}).get("client_name_display")
    for cand in _as_list(sel):
        try:
            el = pg.query_selector(cand)
            if el:
                text = (el.inner_text() or "").strip()
                if text:
                    return text
        except Exception:  # noqa: BLE001
            continue
    try:
        return pg.title() or ""
    except Exception:  # noqa: BLE001
        return ""


def _verify_client(P, cfg: dict, task: DownloadTask, sleep, say) -> None:
    """화면이 **그 수임처**로 바뀌었는지 확인한다.

    위하고는 사업자를 전환하는 방식이라, 전환이 안 되면 이전 수임처 화면이 그대로
    남는다. 그걸 모르고 받으면 남의 자료를 이 이름으로 저장하게 된다.
    """
    if not cfg.get("verify_client_on_screen", True) or task.here:
        return
    import time as _time

    deadline = _time.monotonic() + int(cfg.get("verify_timeout_ms", 10000)) / 1000
    seen = ""
    while True:
        seen = _screen_client_name(P(), cfg)
        if same_client(task.client, seen):
            return
        if _time.monotonic() >= deadline:
            break
        sleep(0.5)
    raise WrongClient(
        "화면이 받으려는 수임처로 바뀌지 않았습니다(사업자 전환 실패로 보입니다). "
        "그대로 받으면 다른 수임처 자료가 섞입니다.")


def _goto(pg, url: str, timeout: int, cfg: dict) -> None:
    """주소로 이동. 해시만 다른 주소는 SPA 가 재로딩하지 않아 화면이 안 바뀐다.

    그래서 빈 페이지를 한 번 거쳐 **항상 새로 읽게** 한다(hard_navigate).
    """
    if cfg.get("hard_navigate", True):
        try:
            pg.goto("about:blank", timeout=timeout)
        except Exception:  # noqa: BLE001 — 못 거쳐도 아래에서 그대로 시도
            pass
    pg.goto(url, timeout=timeout)


def _ensure_ledger_screen(P, cfg: dict, sleep, say) -> None:
    """신용카드 조회 화면까지 확실히 들어간다.

    수임처 주소로 바로 가면 대개 이 화면이 뜬다. 회계 첫 화면이 뜨는 경우
    (또는 목록에서 '회계' 버튼으로 새 탭이 열린 경우)에는 **'신용카드' 를 한 번 더**
    눌러야 조회 화면이 나온다(2026-08-30 확인).
    """
    sel = cfg.get("selectors", {}) or {}
    full = int(cfg.get("ready_timeout_ms", 30000))
    quick = int(cfg.get("ledger_quick_ms", 6000))
    try:
        _wait_ready(P, sel.get("search_button"), quick, "신용카드 화면",
                    sleep=sleep, say=say)
        return
    except NotReady:
        pass

    menu = _as_list(sel.get("ledger_menu"))
    if menu:
        say("회계 첫 화면 — '신용카드' 로 들어갑니다")
        try:
            _click_any(P(), menu, quick, "신용카드 메뉴")
        except Exception as e:  # noqa: BLE001 — 못 눌러도 아래에서 한 번 더 기다린다
            say(f"'신용카드' 메뉴를 못 눌렀습니다({type(e).__name__})")
    _wait_ready(P, sel.get("search_button"), full, "신용카드 화면",
                sleep=sleep, say=say)


def _failure_kind(exc: Exception) -> str:
    """실패를 사람이 읽을 수 있는 갈래로 — 예외 목록을 만들 때 쓴다."""
    text = str(exc)
    if isinstance(exc, WrongClient):
        return "다른 수임처 화면"
    if isinstance(exc, NotReady):
        return "화면 준비 안 됨"
    if isinstance(exc, NoAppPage):
        return "탭을 못 찾음"
    if isinstance(exc, SessionExpired):
        return "로그인 만료"
    if isinstance(exc, StepFailed):
        head = text.split("]", 1)[0].lstrip("[")
        return f"막힘: {head}" if head and len(head) < 24 else "막힘"
    return f"오류: {type(exc).__name__}"


def _close_ledger(P, cfg: dict, task: DownloadTask, say) -> None:
    """다 쓴 회계 탭을 닫는다 — **새 탭으로 열렸을 때만**.

    주소로 이동하는 방식은 같은 탭 안에서 움직이므로, 닫으면 그게 유일한 탭이라
    다음 수임처부터 전부 죽는다(실측 2026-08-31: 131곳이 '탭을 못 찾음'으로 실패).
    """
    if not cfg.get("close_ledger_after") or task.here or task.url:
        return
    from kafa.fetch.inspect import pages_of

    pg = P()
    try:
        others = [x for x in pages_of(pg) if _is_open(x) and x is not pg]
    except Exception:  # noqa: BLE001
        others = []
    if not others:
        return                      # 마지막 남은 탭은 닫지 않는다
    try:
        say("회계 탭 닫기")
        pg.close()
    except Exception:  # noqa: BLE001 — 못 닫아도 계속
        pass


# 수임처 목록에서 **그 수임처 행의 '회계' 버튼**을 누른다.
# 이름 링크를 누르면 수임처 정보 화면으로 가버린다(2026-08-30 확인).
# 행을 CSS 로 집기 어려워, 이름 링크에서 위로 올라가며 같은 행 안의 버튼을 찾는다.
# 위로 올라가다 **처음** 만나는 것이 그 행의 버튼이므로 다른 수임처를 열 위험이 없다.
_OPEN_CLIENT_JS = r"""
([cno, name, label]) => {
  let a = null;
  if (cno) a = document.getElementById('tooltip_' + cno);
  if (!a && name) {
    for (const el of document.querySelectorAll('a[id^="tooltip_"]')) {
      if ((el.textContent || '').trim() === name) { a = el; break; }
    }
  }
  if (!a) return 'no-anchor';
  let el = a;
  for (let d = 0; el && d < 8; d++, el = el.parentElement) {
    for (const b of el.querySelectorAll('button, a')) {
      if ((b.textContent || '').trim() === label) { b.click(); return 'ok'; }
    }
  }
  return 'no-button';
}
"""


def _click_row_button(pg, cno: str, name: str, label: str) -> str:
    try:
        return str(pg.evaluate(_OPEN_CLIENT_JS, [cno, name, label]))
    except Exception as e:  # noqa: BLE001
        return f"error:{type(e).__name__}"


def _open_client(P, cfg: dict, task: DownloadTask, timeout: int, say):
    """수임처 목록에서 검색해 해당 수임처의 회계 화면을 연다.

    목록 링크는 `a#tooltip_<수임처코드>` 라서 코드가 있으면 이름 중복과 무관하게
    정확히 집을 수 있다. 코드가 없으면 이름으로 찾는다.
    """
    sel = cfg.get("selectors", {}) or {}

    def _dash():
        try:
            return pick_page(P(), cfg, want="dashboard")
        except Exception:  # noqa: BLE001 — 목록 탭을 못 고르면 지금 탭으로
            return P()

    dash = _dash()
    say("수임처 목록에서 검색")
    search_input = _first_selector(sel.get("client_search_input"))
    if search_input:
        _step("수임처 검색", search_input,
              lambda: dash.fill(search_input, task.client, timeout=timeout))
    # 그 수임처 행의 '회계' 버튼 — 검색 결과가 그려질 때까지 잠깐 기다린다.
    label = str(cfg.get("client_open_button_text", "")).strip()
    if label:
        import time as _time

        deadline = _time.monotonic() + int(cfg.get("ready_timeout_ms", 30000)) / 1000
        last = ""
        while True:
            last = _click_row_button(dash, task.cno, task.client, label)
            if last == "ok":
                say(f"수임처 행의 '{label}' 버튼 클릭")
                return
            if _time.monotonic() >= deadline:
                break
            _time.sleep(0.5)
            dash = _dash()
        raise StepFailed(f"[수임처 선택] 목록에서 '{label}' 버튼을 못 찾았습니다({last}). "
                         "검색 결과가 안 떴거나 수임처코드가 다를 수 있습니다.")

    # (설정을 비우면) 예전 방식 — 이름 링크를 누른다
    if task.cno:
        item = _first_selector(sel.get("client_result_by_cno")) \
            .replace("{cno}", task.cno)
    else:
        item = _first_selector(sel.get("client_result_item")) \
            .replace("{client}", task.client)
    if not item:
        raise StepFailed("[수임처 선택] 목록 항목 selector 가 설정되지 않았습니다")
    say(f"수임처 열기 {item}")
    _step("수임처 선택", item, lambda: dash.click(item, timeout=timeout))


def _close_dropdown(page) -> None:
    """열린 채로 남은 목록을 닫는다 — 안 닫으면 조회 버튼을 덮어 못 누른다.

    실측(2026-08-31): 구분 선택에 실패한 수임처에서 곧바로 '[조회] 못 눌렀습니다' 가
    이어졌다. 목록이 펼쳐진 채 버튼을 가린 것으로 보인다.
    """
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001 — 키보드를 못 쓰면 그냥 넘어간다
        pass


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
    other = str(cfg.get("kind_current_other", "")).strip()
    tries = int(cfg.get("kind_try_timeout_ms", 4000))
    for cand in openers:
        if "{other}" in cand:
            if not other:
                continue
            cand = cand.replace("{other}", other)
        try:
            page.click(cand, timeout=tries)
            page.click(target, timeout=tries)
            say(f"구분 선택 '{kind}'")
            return
        except Exception:  # noqa: BLE001 — 다음 후보로
            _close_dropdown(page)
            continue
    _close_dropdown(page)
    say("구분을 자동으로 못 맞췄습니다 — 받은 파일 이름으로 확인합니다")


def run_fetch(page, plan: DownloadPlan, inbox, *, cfg: Optional[dict] = None,
              sleep: Callable[[float], None] = None,
              on_progress: Optional[Callable[[DownloadTask, str], None]] = None,
              on_session_expired: Optional[Callable[[], None]] = None,
              on_step: Optional[Callable[[str], None]] = None,
              on_failure: Optional[Callable[[DownloadTask, Exception], None]] = None,
              download: bool = True,
              on_capture: Optional[Callable] = None
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

    attempts = max(1, int(cfg.get("task_retries", 2)) + 1)
    retry_wait = float(cfg.get("retry_wait_seconds", 5.0))

    def _once(task):
        return fetch_one(pick_page(page, cfg), cfg, task,
                         target_path(inbox, task), on_step=on_step,
                         resolve=lambda: pick_page(page, cfg), sleep=sleep,
                         download=download,
                         on_capture=(lambda pg, kind: on_capture(pg, task, kind))
                         if on_capture else None)

    for i, task in enumerate(plan.tasks):
        label = f"{task.client}/{task.period}"
        last = None
        for attempt in range(attempts):
            try:
                try:
                    dest = _once(task)
                except SessionExpired:
                    if on_session_expired is None:
                        raise
                    on_session_expired()          # 사람이 다시 로그인
                    dest = _once(task)
                if download:
                    res.saved.append(dest)
                res.probed[label] = "자료 있음"
                if on_progress:
                    on_progress(task, "자료 있음" if not download else "저장")
                last = None
                break
            except NoData:
                # 자료가 없는 달·수임처는 실패가 아니다. 다시 시도하지 않는다.
                res.empty.append(label)
                res.probed[label] = "자료 없음"
                if on_progress:
                    on_progress(task, "자료 없음")
                last = None
                break
            except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않음
                last = e
                if attempt < attempts - 1:
                    res.retried += 1
                    if on_progress:
                        on_progress(task, f"재시도 {attempt + 1}/{attempts - 1}"
                                          f"({type(e).__name__})")
                    sleep(retry_wait)
        if last is not None:
            res.failures[label] = f"{type(last).__name__}: {last}"
            kind = _failure_kind(last)
            res.probed[label] = kind
            # 화면 사진은 fetch_one 이 **막힌 그 순간** 찍는다(여기서 찍으면 이미
            # 화면이 바뀐 뒤라 원인이 안 보인다). 다만 fetch_one 에 들어가기도 전에
            # 실패한 경우(탭을 못 찾음 등)는 여기서 한 번 남긴다.
            if on_capture and isinstance(last, NoAppPage):
                try:
                    on_capture(pick_page(page, cfg), task, kind)
                except Exception:  # noqa: BLE001 — 사진 실패가 수집을 막지 않는다
                    pass
            if on_progress:
                on_progress(task, f"실패({type(last).__name__})")
            if on_failure:
                on_failure(task, last)
        if i < len(plan.tasks) - 1:
            sleep(delay)          # 서버 부담 완화
    return res
