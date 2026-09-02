"""감독형 수집 — 계획/경로/보정가드/순회. 브라우저 없이 가짜 page 로 검증."""
from datetime import date
from pathlib import Path

import pytest

from kafa.fetch.plan import (DownloadTask, build_plan, months_between,
                             recent_months, safe_name, target_path)
from kafa.fetch.wehago import (NotCalibrated, format_period, missing_selectors,
                               run_fetch)


# ── 계획 ──

def test_months_between_and_recent():
    assert months_between("2025-11", "2026-02") == ["2025-11", "2025-12",
                                                    "2026-01", "2026-02"]
    assert months_between("2026-03", "2026-01") == []      # 역순 → 빈 목록
    assert recent_months(3, today=date(2026, 2, 15)) == ["2025-12", "2026-01", "2026-02"]


def test_safe_name_blocks_path_tricks():
    # 경로 구분자 치환 + 앞뒤 점/공백 제거 → 상위 폴더로 빠져나갈 수 없다
    out = safe_name("../../etc")
    assert "/" not in out and "\\" not in out and not out.startswith(".")
    assert safe_name('가/나:다') == "가_나_다"
    assert safe_name("   ") == "unknown"


def test_target_path_matches_pipeline_layout(tmp_path):
    p = target_path(tmp_path, DownloadTask("행복상사", "2026-03"))
    assert p == tmp_path / "행복상사" / "2026-03.xlsx"      # inbox/<고객>/<기간>.xlsx


def test_build_plan_skips_already_downloaded(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "A").mkdir(parents=True)
    (inbox / "A" / "2026-01.xlsx").write_text("x")
    plan = build_plan(inbox, ["A", "B"], ["2026-01", "2026-02"])
    assert plan.total == 4 and len(plan.skipped) == 1
    assert DownloadTask("A", "2026-01") in plan.skipped


def test_build_plan_skips_archived(tmp_path):
    """이미 처리해 아카이브로 옮긴 것도 다시 받지 않는다."""
    inbox, arch = tmp_path / "inbox", tmp_path / "out" / "_archive"
    (arch / "A").mkdir(parents=True)
    (arch / "A" / "2026-01.xlsx").write_text("x")
    plan = build_plan(inbox, ["A"], ["2026-01", "2026-02"], archive=arch)
    assert len(plan.tasks) == 1 and plan.tasks[0].period == "2026-02"


# ── 보정 가드 ──

def test_missing_selectors_detects_todo():
    cfg = {"selectors": {"client_search_input": "TODO", "client_result_item": "",
                         "period_from_input": "#a", "period_to_input": "#b",
                         "search_button": "#c", "excel_download_button": "#d"}}
    assert missing_selectors(cfg) == ["client_search_input", "client_result_item"]


def test_shipped_config_is_calibrated_for_screen_url_mode():
    """실화면 기록으로 보정된 경로(URL 이동 + 화면 기간)는 바로 쓸 수 있어야 한다."""
    from kafa.fetch.wehago import load_fetch_config
    cfg = load_fetch_config()
    assert cfg["period_mode"] == "screen"
    assert missing_selectors(cfg, url_mode=True) == []


def test_shipped_config_supports_dashboard_navigation():
    """수임처 목록에서 검색·클릭하는 경로도 보정 완료(실화면 확인)."""
    from kafa.fetch.wehago import load_fetch_config
    cfg = load_fetch_config()
    assert missing_selectors(cfg, url_mode=False) == []
    assert "tooltip_{cno}" in cfg["selectors"]["client_result_by_cno"]


def test_shipped_config_still_blocks_calendar_mode():
    """확인하지 못한 경로(달력 월지정)는 여전히 실행을 막는다."""
    from kafa.fetch.wehago import load_fetch_config
    cfg = load_fetch_config()
    cfg["period_mode"] = "calendar"
    assert missing_selectors(cfg, url_mode=True)            # 달력 미보정


def test_run_fetch_refuses_when_not_calibrated(tmp_path):
    plan = build_plan(tmp_path, ["A"], ["2026-01"])
    with pytest.raises(NotCalibrated):
        run_fetch(object(), plan, tmp_path, cfg={"selectors": {}})


def test_format_period():
    assert format_period("2026-03", "%Y-%m") == "2026-03"
    assert format_period("2026-03", "%Y%m") == "202603"
    assert format_period("2026-03", "%Y.%m") == "2026.03"


# ── 순회(가짜 page) ──

_CFG = {"selectors": {"client_search_input": "#s", "client_result_item": "#r-{client}",
                      "client_result_by_cno": "a#tooltip_{cno}",
                      "period_from_input": "#f", "period_to_input": "#t",
                      "search_button": "#go", "excel_download_button": "#xls"},
        "delay_seconds": 0, "timeout_ms": 100, "period_format": "%Y-%m",
        "ready_timeout_ms": 200, "after_search_seconds": 0,
        "menu_retry_seconds": 0, "verify_client_on_screen": False}


class _FakeDownload:
    suggested_filename = "신용카드(매입)_20260831.xlsx"

    def __init__(self, log): self.log = log
    def save_as(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("xlsx", encoding="utf-8")
        self.log.append(("saved", str(path)))


class _FakeExpect:
    def __init__(self, log): self.value = _FakeDownload(log)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakePage:
    """클릭/입력을 기록만 하는 가짜 페이지. fail_on 에 걸리면 예외."""
    def __init__(self, fail_on=None, present=()):
        self.log, self.fail_on = [], fail_on or set()
        # 검색창(#s)·조회 버튼(#go)은 기본으로 '있다' — 화면 준비 대기를 통과시킨다
        self.present = set(present) | {"#s", "#go"}
        self.context = None

    def is_closed(self):
        return False

    def query_selector(self, sel):
        return object() if sel in self.present else None
    def fill(self, sel, val, **kw):
        if val in self.fail_on:
            raise RuntimeError(f"입력 실패: {val}")
        self.log.append(("fill", sel, val))
    def click(self, sel, **kw):
        kind = "rightclick" if kw.get("button") == "right" else "click"
        self.log.append((kind, sel))
    def expect_download(self, **kw): return _FakeExpect(self.log)


def test_run_fetch_saves_each_task(tmp_path):
    page = _FakePage()
    plan = build_plan(tmp_path, ["행복상사"], ["2026-01", "2026-02"])
    res = run_fetch(page, plan, tmp_path, cfg=_CFG, sleep=lambda _: None)
    assert res.ok and len(res.saved) == 2
    assert (tmp_path / "행복상사" / "2026-01.xlsx").exists()
    # 거래처명이 selector 에 치환되어 클릭됨
    assert ("click", "#r-행복상사") in page.log


def test_run_fetch_continues_after_failure(tmp_path):
    page = _FakePage(fail_on={"실패상사"})
    plan = build_plan(tmp_path, ["실패상사", "정상상사"], ["2026-01"])
    res = run_fetch(page, plan, tmp_path, cfg=_CFG, sleep=lambda _: None)
    assert not res.ok and len(res.failures) == 1 and len(res.saved) == 1
    assert (tmp_path / "정상상사" / "2026-01.xlsx").exists()


def test_run_fetch_reports_skipped(tmp_path):
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "2026-01.xlsx").write_text("x")
    plan = build_plan(tmp_path, ["A"], ["2026-01"])
    res = run_fetch(_FakePage(), plan, tmp_path, cfg=_CFG, sleep=lambda _: None)
    assert res.skipped == 1 and res.saved == []


# ── URL 이동 모드(수임처 마스터) ──

_CFG_URL = {"selectors": {"period_from_input": "#f", "period_to_input": "#t",
                          "search_button": "#go", "excel_download_button": "#xls",
                          "login_marker": "#login"},
            "delay_seconds": 0, "timeout_ms": 100, "period_format": "%Y-%m",
        "ready_timeout_ms": 200, "after_search_seconds": 0,
        "menu_retry_seconds": 0, "verify_client_on_screen": False}


class _UrlPage(_FakePage):
    """goto 를 기록하고, login_hits 만큼 로그인 화면을 흉내낸다."""
    def __init__(self, login_hits=0, present=()):
        super().__init__(present=present)
        self.goto_urls = []
        self.login_hits = login_hits
    def goto(self, url, **kw):
        self.goto_urls.append(url)

    @property
    def real_gotos(self):
        """about:blank 는 SPA 재로딩용 경유지 — 실제 이동만 본다."""
        return [u for u in self.goto_urls if u != "about:blank"]
    def query_selector(self, sel):
        if sel == "#login" and self.login_hits > 0:
            self.login_hits -= 1
            return object()          # 로그인 화면 감지
        return super().query_selector(sel)


def test_url_mode_navigates_directly_and_skips_search_selectors(tmp_path):
    from kafa.fetch.wehago import missing_selectors
    # 검색 관련 selector 가 없어도 URL 모드면 실행 가능해야 한다
    assert missing_selectors(_CFG_URL, url_mode=True) == []
    assert "client_search_input" in missing_selectors(_CFG_URL, url_mode=False)

    page = _UrlPage()
    plan = build_plan(tmp_path, ["행복상사"], ["2026-01"],
                      urls={"행복상사": "https://x/acct?cno=1"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_URL, sleep=lambda _: None)
    assert res.ok and page.real_gotos == ["https://x/acct?cno=1"]
    assert ("fill", "#s", "행복상사") not in page.log      # 검색창 안 씀


def test_session_expiry_asks_human_then_retries(tmp_path):
    page = _UrlPage(login_hits=1)          # 첫 시도에 로그인 화면
    asked = []
    plan = build_plan(tmp_path, ["행복상사"], ["2026-01"],
                      urls={"행복상사": "https://x/1"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_URL, sleep=lambda _: None,
                    on_session_expired=lambda: asked.append(True))
    assert asked == [True] and res.ok       # 사람이 재로그인 후 성공
    assert len(page.real_gotos) == 2        # 재시도


def test_session_expiry_without_handler_is_a_failure(tmp_path):
    page = _UrlPage(login_hits=5)
    plan = build_plan(tmp_path, ["행복상사"], ["2026-01"], urls={"행복상사": "https://x/1"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_URL, sleep=lambda _: None)
    assert not res.ok and "SessionExpired" in str(res.failures)


# ── 화면 기간(screen) 모드 — 달력 조작 없이 기수 전체를 한 번에 ──

_CFG_SCREEN = {"period_mode": "screen",
               "kind_value": "2. 매입",
               "selectors": {
                   "kind_select_open": "#kindopen",
                   "kind_option": "li a:has-text(\"{kind}\")",
                   "search_button": "#go",
                   "excel_download_button": "#xls",
                   "download_confirm": "#confirm"},
               "delay_seconds": 0, "timeout_ms": 100,
               "ready_timeout_ms": 200, "after_search_seconds": 0,
               "menu_retry_seconds": 0, "verify_client_on_screen": False}


def test_screen_mode_skips_calendar_and_picks_purchase(tmp_path):
    from kafa.fetch.wehago import fetch_one
    # kind_autoselect 를 켜면 목록을 열어 매입을 고른다(기본은 꺼짐 — 아래 별도 테스트)
    cfg = {**_CFG_SCREEN, "kind_autoselect": True}
    page = _UrlPage()
    task = DownloadTask("행복상사", "2026", url="https://x/1")
    fetch_one(page, cfg, task, tmp_path / "행복상사" / "2026.xlsx")
    kinds = [e for e in page.log if e[0] == "click"]
    assert ("click", "#kindopen") in kinds
    assert ("click", 'li a:has-text("2. 매입")') in kinds
    assert ("click", "#go") in kinds and ("click", "#xls") in kinds
    # 변환 완료 알림도 닫는다(안 닫으면 다음 건이 가려짐)
    assert ("click", "#confirm") in kinds
    # 기간 입력은 건드리지 않는다
    assert not [e for e in page.log if e[0] == "fill"]


def test_screen_mode_survives_missing_confirm_dialog(tmp_path):
    """알림이 안 뜨는 경우에도 저장은 성공해야 한다."""
    from kafa.fetch.wehago import fetch_one

    class _NoConfirm(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#confirm":
                raise RuntimeError("no dialog")
            super().click(sel, **kw)

    page = _NoConfirm()
    dest = tmp_path / "행복상사" / "2026.xlsx"
    fetch_one(page, _CFG_SCREEN, DownloadTask("행복상사", "2026", url="https://x/1"), dest)
    assert dest.exists()


def test_kind_selection_skipped_when_not_configured(tmp_path):
    from kafa.fetch.wehago import fetch_one
    cfg = {**_CFG_SCREEN, "selectors": {**_CFG_SCREEN["selectors"],
                                        "kind_select_open": "TODO"}}
    page = _UrlPage()
    fetch_one(page, cfg, DownloadTask("A", "2026", url="https://x/1"),
              tmp_path / "A" / "2026.xlsx")
    assert not [e for e in page.log if e[1] == "TODO"]


# ── --here: 이동 없이 열어 둔 화면에서 받기 ──

def test_here_mode_does_not_navigate(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage()
    dest = tmp_path / "행복상사" / "2026.xlsx"
    fetch_one(page, _CFG_SCREEN, DownloadTask("행복상사", "2026", here=True), dest)
    assert page.goto_urls == []                      # 주소 이동 없음
    assert not [e for e in page.log if e[1] == "#s"]  # 화면 검색도 안 함
    assert dest.exists()


def test_here_mode_needs_no_search_selectors():
    cfg = {**_CFG_SCREEN}
    assert missing_selectors(cfg, url_mode=True) == []


def test_here_mode_passes_calibration_guard_in_run_fetch(tmp_path):
    """--here 는 화면 검색을 하지 않으므로 검색 selector 를 요구하면 안 된다."""
    from dataclasses import replace
    plan = build_plan(tmp_path, ["행복상사"], ["2026"])
    plan.tasks[:] = [replace(t, here=True) for t in plan.tasks]
    res = run_fetch(_UrlPage(), plan, tmp_path, cfg=_CFG_SCREEN, sleep=lambda _: None)
    assert res.ok and (tmp_path / "행복상사" / "2026.xlsx").exists()


# ── 실패 진단: 어느 단계에서 막혔는지 ──

def test_failure_names_the_step_and_selector(tmp_path):
    from kafa.fetch.wehago import StepFailed, fetch_one

    class _NoSearch(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#go":
                raise TimeoutError("Timeout 100ms exceeded")
            super().click(sel, **kw)

    try:
        fetch_one(_NoSearch(), _CFG_SCREEN, DownloadTask("A", "2026", here=True),
                  tmp_path / "A" / "2026.xlsx")
    except StepFailed as e:
        assert "[조회]" in str(e) and "#go" in str(e)
    else:
        raise AssertionError("StepFailed 가 나와야 한다")


def test_run_fetch_reports_steps_and_failure(tmp_path):
    class _Broken(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#xls":
                raise TimeoutError("nope")
            super().click(sel, **kw)

    steps, failures = [], []
    plan = build_plan(tmp_path, ["A"], ["2026"])
    from dataclasses import replace
    plan.tasks[:] = [replace(t, here=True) for t in plan.tasks]
    res = run_fetch(_Broken(), plan, tmp_path, cfg=_CFG_SCREEN, sleep=lambda _: None,
                    on_step=steps.append,
                    on_failure=lambda t, e: failures.append((t.client, str(e))))
    assert not res.ok
    assert "조회" in steps and "엑셀 변환·다운로드" in steps
    assert failures and "엑셀 다운로드" in failures[0][1]


# ── 조작할 탭 고르기(회계 모듈이 새 창으로 열리고 원래 탭이 닫히는 경우) ──

class _Tab:
    def __init__(self, url, *, closed=False, has=None):
        self._url, self._closed, self._has = url, closed, set(has or [])
        self.context = None

    @property
    def url(self):
        return self._url

    def is_closed(self):
        return self._closed

    def query_selector(self, sel):
        return object() if sel in self._has else None


class _Ctx:
    def __init__(self, pages):
        self.pages = [p for p in pages if not p.is_closed()]


_PICK_CFG = {"page_url_hint": "smarta.wehago.com",
             "ignore_url_parts": ["adtrafficquality.google", "about:blank"],
             "selectors": {"search_button": "#go"}}


def test_pick_page_finds_ledger_tab_when_original_closed():
    from kafa.fetch.wehago import pick_page
    dead = _Tab("about:blank", closed=True)
    ad = _Tab("https://ep2.adtrafficquality.google/sodar/runner.html")
    real = _Tab("https://smarta.wehago.com/#/smarta/account/SAAC0105", has=["#go"])
    dead.context = _Ctx([dead, ad, real])
    assert pick_page(dead, _PICK_CFG) is real


def test_pick_page_ignores_ad_tabs():
    from kafa.fetch.wehago import NoAppPage, pick_page
    dead = _Tab("about:blank", closed=True)
    ad = _Tab("https://ep2.adtrafficquality.google/sodar/runner.html")
    dead.context = _Ctx([dead, ad])
    try:
        pick_page(dead, _PICK_CFG)
    except NoAppPage as e:
        assert "탭을 찾지 못했습니다" in str(e)
    else:
        raise AssertionError("NoAppPage 가 나와야 한다")


def test_pick_page_keeps_current_tab_before_navigation():
    """아직 이동 전(화면에 조회 버튼이 없음)이면 원래 탭을 그대로 쓴다."""
    from kafa.fetch.wehago import pick_page
    cur = _Tab("https://www.wehago.com/#/main")
    cur.context = _Ctx([cur])
    assert pick_page(cur, _PICK_CFG) is cur


# ── 구분(매입/매출) — 확인 먼저, 못 맞추면 파일 이름으로 검증 ──

_CFG_KIND = {**_CFG_SCREEN,
             "selectors": {**_CFG_SCREEN["selectors"],
                           "kind_current": 'text="{kind}"',
                           "kind_select_open": ["#nope", "#kindopen"]},
             "kind_try_timeout_ms": 10,
             "expect_filename_contains": "매입"}


def test_kind_already_set_is_left_alone(tmp_path):
    """이미 매입이면 드롭다운을 건드리지 않는다(엉뚱한 목록을 여는 사고 방지)."""
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage(present={'text="2. 매입"'})
    fetch_one(page, _CFG_KIND, DownloadTask("A", "2026", here=True),
              tmp_path / "A" / "2026.xlsx")
    assert not [e for e in page.log if e[1] in ("#nope", "#kindopen")]


def test_kind_tries_candidates_in_order(tmp_path):
    from kafa.fetch.wehago import fetch_one

    class _FirstFails(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#nope":
                raise TimeoutError("not visible")
            super().click(sel, **kw)

    page = _FirstFails()
    fetch_one(page, {**_CFG_KIND, "kind_autoselect": True},
              DownloadTask("A", "2026", here=True), tmp_path / "A" / "2026.xlsx")
    assert ("click", "#kindopen") in page.log


def test_kind_failure_does_not_block_but_filename_is_checked(tmp_path):
    """구분을 못 맞춰도 진행하되, 매출 파일이 오면 저장하지 않고 실패시킨다."""
    from kafa.fetch.wehago import StepFailed, fetch_one

    class _SalesFile(_UrlPage):
        def click(self, sel, **kw):
            if sel in ("#nope", "#kindopen"):
                raise TimeoutError("not visible")
            super().click(sel, **kw)

        def expect_download(self, **kw):
            exp = _FakeExpect(self.log)
            exp.value.suggested_filename = "신용카드(매출)_20260831.xlsx"
            return exp

    dest = tmp_path / "A" / "2026.xlsx"
    try:
        fetch_one(_SalesFile(), _CFG_KIND, DownloadTask("A", "2026", here=True), dest)
    except StepFailed as e:
        assert "'매입' 자료가 아닙니다" in str(e)
        assert not dest.exists()          # 잘못된 파일은 남기지 않는다
    else:
        raise AssertionError("StepFailed 가 나와야 한다")


def test_kind_autoselect_off_by_default_touches_nothing(tmp_path):
    """기본값에서는 드롭다운을 전혀 누르지 않는다(엉뚱한 클릭 방지)."""
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage()          # 현재 구분을 확인할 수 없는 상태
    fetch_one(page, _CFG_KIND, DownloadTask("A", "2026", here=True),
              tmp_path / "A" / "2026.xlsx")
    assert not [e for e in page.log if e[1] in ("#nope", "#kindopen")]


def test_steps_use_freshly_resolved_page(tmp_path):
    """붙잡아 둔 탭이 죽어도 단계마다 살아 있는 탭을 다시 골라 진행한다."""
    from kafa.fetch.wehago import fetch_one

    dead = _UrlPage()
    dead.click = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("Target page, context or browser has been closed"))
    alive = _UrlPage()
    fetch_one(dead, _CFG_KIND, DownloadTask("A", "2026", here=True),
              tmp_path / "A" / "2026.xlsx", resolve=lambda: alive)
    assert ("click", "#go") in alive.log and ("click", "#xls") in alive.log


# ── 후보 selector 여러 개 시도 ──

def test_click_any_falls_back_to_next_candidate(tmp_path):
    from kafa.fetch.wehago import fetch_one

    class _OnlySecond(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#first":
                raise TimeoutError("없음")
            super().click(sel, **kw)

    cfg = {**_CFG_KIND, "selectors": {**_CFG_KIND["selectors"],
                                      "excel_download_button": ["#first", "#xls"]}}
    page = _OnlySecond()
    dest = tmp_path / "A" / "2026.xlsx"
    fetch_one(page, cfg, DownloadTask("A", "2026", here=True), dest)
    assert ("click", "#xls") in page.log and dest.exists()


def test_click_any_reports_all_tried_selectors(tmp_path):
    from kafa.fetch.wehago import StepFailed, fetch_one

    class _NoneWork(_UrlPage):
        def click(self, sel, **kw):
            if sel in ("#a", "#b"):
                raise TimeoutError("없음")
            super().click(sel, **kw)

    cfg = {**_CFG_KIND, "selectors": {**_CFG_KIND["selectors"],
                                      "excel_download_button": ["#a", "#b"]}}
    try:
        fetch_one(_NoneWork(), cfg, DownloadTask("A", "2026", here=True),
                  tmp_path / "A" / "2026.xlsx")
    except StepFailed as e:
        assert "#a" in str(e) and "#b" in str(e) and "모두 시도" in str(e)
    else:
        raise AssertionError("StepFailed 가 나와야 한다")


def test_shipped_config_download_has_candidates():
    from kafa.fetch.wehago import _as_list, load_fetch_config
    cands = _as_list(load_fetch_config()["selectors"]["excel_download_button"])
    assert len(cands) >= 2      # 하나가 안 맞아도 다음을 시도한다


# ── '엑셀변환' 은 표에서 우클릭해야 나오는 메뉴 안에 있다 ──

_CFG_CTX = {**_CFG_KIND,
            "selectors": {**_CFG_KIND["selectors"],
                          "excel_context_target": ["div#GRID_TOP canvas"],
                          "excel_download_button": ["#xls"]}}


def _ctx_page(**kw):
    """표(우클릭 대상)가 이미 그려져 있는 가짜 화면."""
    return _UrlPage(present={"div#GRID_TOP canvas", "div#GRID_TOP"}, **kw)


def test_right_clicks_grid_before_excel_menu(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _ctx_page()
    dest = tmp_path / "A" / "2026.xlsx"
    fetch_one(page, _CFG_CTX, DownloadTask("A", "2026", here=True), dest)
    assert ("rightclick", "div#GRID_TOP canvas") in page.log
    # 우클릭이 '엑셀변환' 클릭보다 먼저여야 한다
    assert page.log.index(("rightclick", "div#GRID_TOP canvas")) < \
        page.log.index(("click", "#xls"))
    assert dest.exists()


def test_context_target_falls_back_to_next_candidate(tmp_path):
    from kafa.fetch.wehago import fetch_one

    class _CanvasMissing(_UrlPage):
        def click(self, sel, **kw):
            if sel == "div#GRID_TOP canvas":
                raise TimeoutError("없음")
            super().click(sel, **kw)

    cfg = {**_CFG_CTX, "selectors": {
        **_CFG_CTX["selectors"],
        "excel_context_target": ["div#GRID_TOP canvas", "div#GRID_TOP"]}}
    page = _CanvasMissing(present={"div#GRID_TOP canvas", "div#GRID_TOP"})
    fetch_one(page, cfg, DownloadTask("A", "2026", here=True),
              tmp_path / "A" / "2026.xlsx")
    assert ("rightclick", "div#GRID_TOP") in page.log


def test_shipped_config_right_clicks_the_grid():
    from kafa.fetch.wehago import _as_list, load_fetch_config
    assert _as_list(load_fetch_config()["selectors"]["excel_context_target"])


# ── 수임처 목록에서 코드로 정확히 열기 ──

_CFG_NAV = {**_CFG_SCREEN,
            "selectors": {**_CFG_SCREEN["selectors"],
                          "client_search_input": "#s",
                          "client_result_by_cno": "a#tooltip_{cno}",
                          "client_result_item": 'a:has-text("{client}")'},
            "close_ledger_after": False}


def test_opens_client_by_code_when_available(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage(present={"#s"})
    fetch_one(page, _CFG_NAV, DownloadTask("행복상사", "2026", cno="10049328"),
              tmp_path / "행복상사" / "2026.xlsx", resolve=lambda: page)
    assert ("fill", "#s", "행복상사") in page.log
    assert ("click", "a#tooltip_10049328") in page.log


def test_opens_client_by_name_without_code(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage(present={"#s"})
    fetch_one(page, _CFG_NAV, DownloadTask("행복상사", "2026"),
              tmp_path / "행복상사" / "2026.xlsx", resolve=lambda: page)
    assert ("click", 'a:has-text("행복상사")') in page.log


def test_build_plan_carries_client_code():
    plan = build_plan("/tmp/nowhere", ["행복상사"], ["2026"],
                      cnos={"행복상사": "10049328"})
    assert plan.tasks[0].cno == "10049328"


def test_ledger_tab_closed_after_each_client(tmp_path):
    from kafa.fetch.wehago import fetch_one

    class _Closable(_UrlPage):
        closed = False

        def close(self):
            type(self).closed = True

    page = _Closable(present={"#s"})
    other = _UrlPage(present={"#s"})          # 목록 탭이 따로 남아 있다

    class _Ctx:
        pages = [page, other]

    page.context = _Ctx()
    fetch_one(page, {**_CFG_NAV, "close_ledger_after": True},
              DownloadTask("A", "2026", cno="1"), tmp_path / "A" / "2026.xlsx",
              resolve=lambda: page)
    assert _Closable.closed


# ── 마스터 파일 지정 실수에 친절히 ──

def _run_cli(argv):
    """CLI 를 돌리고 (종료코드, stderr) 를 돌려준다."""
    import contextlib
    import io

    from kafa.fetch import cli as fetch_cli
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = fetch_cli.main(argv)
    except SystemExit as e:
        code = e.code
    return code, err.getvalue()


def test_missing_master_file_gives_plain_message(tmp_path):
    code, err = _run_cli(["--inbox", str(tmp_path), "--master",
                          "<마스터 엑셀 경로>", "--whole", "--dry-run"])
    assert code != 0
    assert "수임처 마스터 파일이 없습니다" in err
    assert "Traceback" not in err


def test_unsupported_master_format_is_explained(tmp_path):
    bad = tmp_path / "목록.xls"
    bad.write_text("not really excel", encoding="utf-8")
    code, err = _run_cli(["--inbox", str(tmp_path), "--master", str(bad),
                          "--whole", "--dry-run"])
    assert code != 0 and ".xlsx" in err


# ── 느린 수임처: 기다리고, 다시 시도하고, 자료 없으면 넘어간다 ──

# 목록에서 열기 + 우클릭 메뉴까지 갖춘 설정
_CFG_FULL = {**_CFG_NAV,
             "selectors": {**_CFG_NAV["selectors"],
                           "excel_context_target": ["div#GRID_TOP canvas"],
                           "excel_download_button": ["#xls"]},
             "ready_timeout_ms": 200, "after_search_seconds": 0,
             "menu_retry_seconds": 0}


def test_waits_for_slow_screen_then_proceeds(tmp_path):
    """회계 화면이 늦게 떠도 기다렸다가 진행한다."""
    from kafa.fetch.wehago import fetch_one

    class _Slow(_UrlPage):
        def __init__(self):
            super().__init__(present={"div#GRID_TOP canvas"})
            self.present.discard("#go")
            self.looks = 0

        def query_selector(self, sel):
            if sel == "#go":
                self.looks += 1
                return object() if self.looks > 2 else None
            return super().query_selector(sel)

    page = _Slow()
    dest = tmp_path / "A" / "2026.xlsx"
    fetch_one(page, {**_CFG_FULL, "ready_timeout_ms": 5000},
              DownloadTask("A", "2026", cno="1"), dest,
              resolve=lambda: page, sleep=lambda _s: None)
    assert dest.exists() and page.looks > 2


def test_no_data_is_not_a_failure(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _Empty(_UrlPage):
        searched = False

        def click(self, sel, **kw):
            if sel == "#go":
                type(self).searched = True     # 조회 후에 뜬 팝업만 인정한다
            super().click(sel, **kw)

        def query_selector(self, sel):
            if type(self).searched and sel.startswith("text=") \
                    and "조회된 자료가 없습니다" in sel:
                return object()
            return super().query_selector(sel)

    cfg = {**_CFG_FULL, "empty_result_texts": ["조회된 자료가 없습니다"]}
    plan = build_plan(tmp_path, ["A"], ["2026"], cnos={"A": "1"})
    res = run_fetch(_Empty(), plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and res.empty == ["A/2026"] and res.saved == []


def test_task_is_retried_before_giving_up(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _FlakyFirst(_UrlPage):
        tries = 0

        def click(self, sel, **kw):
            if sel == "#xls":
                type(self).tries += 1
                if type(self).tries == 1:
                    raise TimeoutError("아직 안 뜸")
            super().click(sel, **kw)

    page = _FlakyFirst(present={"div#GRID_TOP canvas"})
    plan = build_plan(tmp_path, ["A"], ["2026"], cnos={"A": "1"})
    cfg = {**_CFG_FULL, "task_retries": 2, "retry_wait_seconds": 0,
           "menu_retries": 1, "context_click_positions": [{"x": 1, "y": 1}],
           "menu_open_seconds": 0}
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and res.retried == 1


def test_menu_reopened_when_not_ready(tmp_path):
    """우클릭 메뉴가 처음엔 안 떠도 다시 열어 본다."""
    from kafa.fetch.wehago import fetch_one

    class _MenuLate(_UrlPage):
        def __init__(self):
            super().__init__(present={"div#GRID_TOP canvas"})
            self.n = 0

        def click(self, sel, **kw):
            if sel == "#xls":
                self.n += 1
                if self.n == 1:
                    raise TimeoutError("메뉴 없음")
            super().click(sel, **kw)

    page = _MenuLate()
    dest = tmp_path / "A" / "2026.xlsx"
    fetch_one(page, _CFG_FULL, DownloadTask("A", "2026", cno="1"), dest,
              resolve=lambda: page, sleep=lambda _s: None)
    assert dest.exists() and page.n == 2


# ── '조회조건에 맞는 데이터가 없습니다' 팝업 ──

def test_empty_popup_is_dismissed_and_counted(tmp_path):
    """팝업을 닫지 않으면 다음 수임처 화면이 가려진다."""
    from kafa.fetch.wehago import run_fetch

    class _Popup(_UrlPage):
        searched = False

        def click(self, sel, **kw):
            if sel == "#go":
                type(self).searched = True      # 조회 후에야 팝업이 뜬다
            super().click(sel, **kw)

        def query_selector(self, sel):
            if type(self).searched and sel.startswith("text=") \
                    and "조회조건에 맞는 데이터가 없습니다" in sel:
                return object()
            return super().query_selector(sel)

    cfg = {**_CFG_FULL,
           "empty_result_texts": ["조회조건에 맞는 데이터가 없습니다"],
           "selectors": {**_CFG_FULL["selectors"], "popup_confirm": ["#ok"]}}
    page = _Popup(present={"div#GRID_TOP canvas"})
    plan = build_plan(tmp_path, ["A"], ["2026"], cnos={"A": "1"})
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and res.empty == ["A/2026"]
    assert ("click", "#ok") in page.log          # 팝업을 닫았다


def test_kind_dropdown_opened_by_current_value_text(tmp_path):
    """비슷한 드롭다운이 여럿이라, 현재 값('1. 매출') 글자를 눌러 목록을 연다."""
    from kafa.fetch.wehago import fetch_one

    cfg = {**_CFG_FULL,
           "kind_autoselect": True,
           "kind_current_other": "1. 매출",
           "kind_try_timeout_ms": 10,
           "selectors": {**_CFG_FULL["selectors"],
                         "kind_current": 'text="{kind}"',
                         "kind_select_open": ['text="{other}"'],
                         "kind_option": 'li a:has-text("{kind}")'}}
    page = _UrlPage(present={"div#GRID_TOP canvas"})
    fetch_one(page, cfg, DownloadTask("A", "2026", cno="1"),
              tmp_path / "A" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert ("click", 'text="1. 매출"') in page.log
    assert ("click", 'li a:has-text("2. 매입")') in page.log


def test_screenshot_is_opt_in(tmp_path, monkeypatch):
    """사진에는 실명·금액이 찍히므로 옵션을 켤 때만 남긴다."""
    import contextlib
    import io

    from kafa.fetch import cli as fetch_cli
    from kafa.fetch import session as fetch_session

    shots = []

    class _Page(_UrlPage):
        def screenshot(self, **kw):
            shots.append(kw.get("path"))

        def click(self, sel, **kw):
            if sel == "#go":
                raise TimeoutError("실패")
            super().click(sel, **kw)

    @contextlib.contextmanager
    def _fake_browser(**_kw):
        yield _Page(present={"#s"})

    from kafa.fetch import wehago as fetch_wehago

    fast = {**_CFG_FULL, "delay_seconds": 0, "retry_wait_seconds": 0,
            "task_retries": 0, "after_search_seconds": 0}
    monkeypatch.setattr(fetch_wehago, "load_fetch_config", lambda *a, **k: fast)
    monkeypatch.setattr(fetch_session, "browser_page", _fake_browser)
    monkeypatch.setattr(fetch_session, "wait_for_human", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)

    argv = ["--inbox", str(tmp_path / "in"), "--clients", "가", "--here",
            "--whole", "--no-keep-open"]
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        fetch_cli.main(argv)
    assert shots == []                       # 기본은 안 찍는다


# ── 점검 모드: 다 돌아보되 받지는 않는다 ──

def test_probe_reports_data_present_without_downloading(tmp_path):
    from kafa.fetch.wehago import run_fetch
    page = _UrlPage(present={"div#GRID_TOP canvas"})
    plan = build_plan(tmp_path, ["가", "나"], ["2026"],
                      cnos={"가": "1", "나": "2"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_FULL, sleep=lambda _: None,
                    download=False)
    assert res.ok and res.saved == []                    # 받지 않았다
    assert res.probed == {"가/2026": "자료 있음", "나/2026": "자료 있음"}
    assert not (tmp_path / "가" / "2026.xlsx").exists()
    assert not [e for e in page.log if e[1] == "#xls"]   # 엑셀은 안 눌렀다


def test_probe_marks_empty_and_stuck_separately(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _Mixed(_UrlPage):
        """'나'(코드 2) 를 열고 조회했을 때만 자료 없음 팝업이 뜬다."""
        def __init__(self, **kw):
            super().__init__(**kw)
            self.opened, self.searched = "", False

        def click(self, sel, **kw):
            if sel.startswith("a#tooltip_"):
                self.opened, self.searched = sel.rsplit("_", 1)[-1], False
            if sel == "#go":
                self.searched = True
            super().click(sel, **kw)

        def query_selector(self, sel):
            if (self.searched and self.opened == "2"
                    and sel.startswith("text=") and "없음" in sel):
                return object()
            return super().query_selector(sel)

    cfg = {**_CFG_FULL, "empty_result_texts": ["없음"],
           "client_open_button_text": ""}
    page = _Mixed(present={"div#GRID_TOP canvas"})
    plan = build_plan(tmp_path, ["가", "나"], ["2026"],
                      cnos={"가": "1", "나": "2"})
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None,
                    download=False)
    assert res.probed["가/2026"] == "자료 있음"
    assert res.probed["나/2026"] == "자료 없음"


def test_probe_records_failure_kind(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _Stuck(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#go":
                raise TimeoutError("안 눌림")
            super().click(sel, **kw)

    cfg = {**_CFG_FULL, "task_retries": 0, "client_open_button_text": ""}
    plan = build_plan(tmp_path, ["가"], ["2026"], cnos={"가": "1"})
    res = run_fetch(_Stuck(present={"div#GRID_TOP canvas"}), plan, tmp_path,
                    cfg=cfg, sleep=lambda _: None, download=False)
    assert res.probed["가/2026"].startswith("막힘")


# ── 목록에서 '회계' 버튼 누르기 ──

def test_opens_accounting_button_of_that_row(tmp_path):
    from kafa.fetch.wehago import fetch_one

    class _Dash(_UrlPage):
        def __init__(self):
            super().__init__(present={"div#GRID_TOP canvas"})
            self.js = []

        def evaluate(self, js, arg=None):
            self.js.append(arg)
            return "ok"

    cfg = {**_CFG_FULL, "client_open_button_text": "회계"}
    page = _Dash()
    fetch_one(page, cfg, DownloadTask("행복상사", "2026", cno="10049328"),
              tmp_path / "행복상사" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert page.js and page.js[0] == ["10049328", "행복상사", "회계"]
    # 이름 링크는 누르지 않는다(수임처 정보 화면으로 새는 것 방지)
    assert not [e for e in page.log if "tooltip_" in str(e[1])]


def test_missing_accounting_button_is_reported(tmp_path):
    from kafa.fetch.wehago import StepFailed, fetch_one

    class _NoButton(_UrlPage):
        def evaluate(self, js, arg=None):
            return "no-button"

    cfg = {**_CFG_FULL, "client_open_button_text": "회계",
           "ready_timeout_ms": 10}
    page = _NoButton(present={"div#GRID_TOP canvas"})
    try:
        fetch_one(page, cfg, DownloadTask("가", "2026", cno="1"),
                  tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
                  sleep=lambda _s: None)
    except StepFailed as e:
        assert "회계" in str(e) and "no-button" in str(e)
    else:
        raise AssertionError("StepFailed 가 나와야 한다")


# ── 화면 사진: 결과마다 한 장씩 ──

def test_capture_called_for_each_outcome(tmp_path):
    """자료있음·자료없음·막힘 모두 사진 대상이다(처음 보는 화면을 놓치지 않게)."""
    from kafa.fetch.wehago import run_fetch

    shots = []

    class _Mixed(_UrlPage):
        """'나'(코드 2) 를 열고 조회했을 때만 자료 없음 팝업이 뜬다."""
        def __init__(self, **kw):
            super().__init__(**kw)
            self.opened, self.searched = "", False

        def click(self, sel, **kw):
            if sel.startswith("a#tooltip_"):
                self.opened, self.searched = sel.rsplit("_", 1)[-1], False
            if sel == "#go":
                self.searched = True
            super().click(sel, **kw)

        def query_selector(self, sel):
            if (self.searched and self.opened == "2"
                    and sel.startswith("text=") and "없음" in sel):
                return object()
            return super().query_selector(sel)

    cfg = {**_CFG_FULL, "empty_result_texts": ["없음"],
           "client_open_button_text": "", "task_retries": 0}
    page = _Mixed(present={"div#GRID_TOP canvas"})
    plan = build_plan(tmp_path, ["가", "나"], ["2026"],
                      cnos={"가": "1", "나": "2"})
    run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None,
              download=False,
              on_capture=lambda pg, task, kind: shots.append((task.client, kind)))
    assert ("가", "자료있음") in shots
    assert ("나", "자료없음") in shots


def test_capture_runs_on_failure_too(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _Stuck(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#go":
                raise TimeoutError("안 눌림")
            super().click(sel, **kw)

    shots = []
    cfg = {**_CFG_FULL, "task_retries": 0, "client_open_button_text": ""}
    plan = build_plan(tmp_path, ["가"], ["2026"], cnos={"가": "1"})
    run_fetch(_Stuck(present={"div#GRID_TOP canvas"}), plan, tmp_path, cfg=cfg,
              sleep=lambda _: None, download=False,
              on_capture=lambda pg, task, kind: shots.append(kind))
    assert shots and shots[0].startswith("막힘")


# ── 주소로 바로 이동 + '신용카드' 한 번 더 누르기 ──

_CFG_URLNAV = {**_CFG_FULL,
               "selectors": {**_CFG_FULL["selectors"],
                             "ledger_menu": ['a:text-is("신용카드")']},
               "ledger_quick_ms": 50, "ready_timeout_ms": 300}


def test_url_goes_straight_without_touching_the_list(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage(present={"div#GRID_TOP canvas"})
    fetch_one(page, _CFG_URLNAV,
              DownloadTask("가", "2026", url="https://x/card?cno=1", cno="1"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert page.real_gotos == ["https://x/card?cno=1"]
    assert not [e for e in page.log if e[1] == "#s"]        # 검색 안 함
    assert not [e for e in page.log if "신용카드" in str(e[1])]  # 메뉴도 안 누름


def test_clicks_credit_card_menu_when_accounting_home_opens(tmp_path):
    """회계 첫 화면이 뜨면 '신용카드' 를 눌러 조회 화면으로 들어간다."""
    from kafa.fetch.wehago import fetch_one

    class _Home(_UrlPage):
        def __init__(self):
            super().__init__(present={"div#GRID_TOP canvas"})
            self.present.discard("#go")

        def click(self, sel, **kw):
            if sel == 'a:text-is("신용카드")':
                self.present.add("#go")      # 메뉴를 누르면 조회 화면이 뜬다
            super().click(sel, **kw)

    page = _Home()
    fetch_one(page, _CFG_URLNAV,
              DownloadTask("가", "2026", url="https://x/acct", cno="1"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert ("click", 'a:text-is("신용카드")') in page.log
    assert ("click", "#xls") in page.log


def test_falls_back_to_list_when_url_does_not_work(tmp_path):
    """주소가 안 통하면 목록에서 여는 길로 되돌아간다."""
    from kafa.fetch.wehago import fetch_one

    class _BadUrl(_UrlPage):
        def __init__(self):
            super().__init__(present={"div#GRID_TOP canvas"})
            self.present.discard("#go")
            self.js = []

        def evaluate(self, js, arg=None):
            self.js.append(arg)
            self.present.add("#go")          # 목록에서 열면 화면이 뜬다
            return "ok"

    cfg = {**_CFG_URLNAV, "client_open_button_text": "회계",
           "selectors": {**_CFG_URLNAV["selectors"], "ledger_menu": []}}
    page = _BadUrl()
    fetch_one(page, cfg, DownloadTask("가", "2026", url="https://x/bad", cno="7"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert page.real_gotos == ["https://x/bad"]
    assert page.js and page.js[0] == ["7", "가", "회계"]   # 목록에서 회계 버튼


# ── 막힌 그 순간의 화면을 남긴다(조회 전 단계 포함) ──

def test_capture_at_the_failing_step_before_search(tmp_path):
    """주소 이동·신용카드 메뉴·구분 선택 등 조회 **전** 실패도 사진에 남는다."""
    from kafa.fetch.wehago import run_fetch

    class _NoLedger(_UrlPage):
        def __init__(self):
            super().__init__(present={"div#GRID_TOP canvas"})
            self.present.discard("#go")      # 신용카드 화면이 끝내 안 뜬다

    shots = []
    cfg = {**_CFG_URLNAV, "task_retries": 0,
           "selectors": {**_CFG_URLNAV["selectors"], "ledger_menu": []}}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    run_fetch(_NoLedger(), plan, tmp_path, cfg=cfg, sleep=lambda _: None,
              download=False,
              on_capture=lambda pg, task, kind: shots.append(kind))
    assert shots == ["화면 준비 안 됨"]


def test_capture_on_every_attempt_not_just_the_last(tmp_path):
    """재시도마다 남긴다 — 매번 다른 화면일 수 있다."""
    from kafa.fetch.wehago import run_fetch

    class _AlwaysStuck(_UrlPage):
        def click(self, sel, **kw):
            if sel == "#go":
                raise TimeoutError("안 눌림")
            super().click(sel, **kw)

    shots = []
    cfg = {**_CFG_URLNAV, "task_retries": 2, "retry_wait_seconds": 0,
           "client_open_button_text": ""}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    run_fetch(_AlwaysStuck(present={"div#GRID_TOP canvas"}), plan, tmp_path,
              cfg=cfg, sleep=lambda _: None, download=False,
              on_capture=lambda pg, task, kind: shots.append(kind))
    assert len(shots) == 3 and all(k.startswith("막힘") for k in shots)


# ── 사업자 전환이 됐는지 확인 (안 하면 남의 자료를 이 이름으로 저장한다) ──

_CFG_VERIFY = {**_CFG_URLNAV, "verify_client_on_screen": True,
               "verify_timeout_ms": 50, "client_open_button_text": "회계"}


class _NamedPage(_UrlPage):
    """탭 제목으로 '지금 어느 수임처 화면인지' 를 알려주는 가짜 페이지."""
    def __init__(self, name, present=("div#GRID_TOP canvas",)):
        super().__init__(present=set(present))
        self.screen_name = name

    def title(self):
        return f"신용카드(2기) - {self.screen_name}"


def test_accepts_screen_when_client_matches(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _NamedPage("(주)행복상사")
    dest = tmp_path / "행복상사" / "2026.xlsx"
    fetch_one(page, _CFG_VERIFY,
              DownloadTask("(주) 행복상사", "2026", url="https://x/a", cno="1"),
              dest, resolve=lambda: page, sleep=lambda _s: None)
    assert dest.exists()          # 띄어쓰기 차이는 같은 곳으로 본다


def test_reopens_from_list_when_screen_is_another_client(tmp_path):
    """전환이 안 돼 이전 수임처가 남아 있으면 목록에서 다시 연다."""
    from kafa.fetch.wehago import fetch_one

    class _Stale(_NamedPage):
        def __init__(self):
            super().__init__("(주)이전상사")
            self.js = []

        def evaluate(self, js, arg=None):
            self.js.append(arg)
            self.screen_name = "(주)행복상사"     # 목록에서 열면 제대로 바뀐다
            return "ok"

    page = _Stale()
    dest = tmp_path / "행복상사" / "2026.xlsx"
    fetch_one(page, _CFG_VERIFY,
              DownloadTask("(주)행복상사", "2026", url="https://x/a", cno="9"),
              dest, resolve=lambda: page, sleep=lambda _s: None)
    assert page.js and page.js[0] == ["9", "(주)행복상사", "회계"]
    assert dest.exists()


def test_refuses_to_save_when_still_another_client(tmp_path):
    """끝내 전환이 안 되면 받지 않는다 — 자료가 섞이는 것보다 실패가 낫다."""
    from kafa.fetch.wehago import WrongClient, fetch_one

    class _NeverSwitches(_NamedPage):
        def __init__(self):
            super().__init__("(주)이전상사")

        def evaluate(self, js, arg=None):
            return "ok"           # 목록에서 눌러도 화면은 그대로

    dest = tmp_path / "행복상사" / "2026.xlsx"
    page = _NeverSwitches()
    try:
        fetch_one(page, _CFG_VERIFY,
                  DownloadTask("(주)행복상사", "2026", url="https://x/a", cno="9"),
                  dest, resolve=lambda: page, sleep=lambda _s: None)
    except WrongClient as e:
        assert "다른 수임처" in str(e) or "바뀌지 않았습니다" in str(e)
        assert not dest.exists()
    else:
        raise AssertionError("WrongClient 가 나와야 한다")


def test_wrong_client_is_its_own_failure_kind(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _NeverSwitches(_NamedPage):
        def __init__(self):
            super().__init__("(주)이전상사")

        def evaluate(self, js, arg=None):
            return "ok"

    shots = []
    cfg = {**_CFG_VERIFY, "task_retries": 0}
    plan = build_plan(tmp_path, ["(주)행복상사"], ["2026"],
                      urls={"(주)행복상사": "https://x/a"},
                      cnos={"(주)행복상사": "9"})
    res = run_fetch(_NeverSwitches(), plan, tmp_path, cfg=cfg,
                    sleep=lambda _: None, download=False,
                    on_capture=lambda pg, task, kind: shots.append(kind))
    assert res.probed["(주)행복상사/2026"] == "다른 수임처 화면"
    assert shots == ["다른 수임처 화면"]


def test_hard_navigate_forces_reload_for_hash_urls(tmp_path):
    """해시만 다른 주소는 SPA 가 재로딩하지 않으므로 빈 페이지를 거친다."""
    from kafa.fetch.wehago import fetch_one
    page = _NamedPage("가")
    fetch_one(page, {**_CFG_VERIFY, "hard_navigate": True},
              DownloadTask("가", "2026", url="https://x/#/a?cno=1", cno="1"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert page.goto_urls == ["about:blank", "https://x/#/a?cno=1"]


def test_company_name_matching_ignores_form_and_spacing():
    from kafa.fetch.wehago import same_client
    assert same_client("(주) 서경디엔시", "신용카드(2기) - (주)서경디엔시")
    assert same_client("행복상사", "신용카드(1기) - 행복상사")
    assert not same_client("행복상사", "신용카드(2기) - (주)튼튼상사")
    assert not same_client("행복상사", "")


# ── 탭을 닫아서 스스로 죽지 않기 ──

def test_url_mode_never_closes_its_only_tab(tmp_path):
    """주소 이동은 같은 탭에서 움직인다 — 닫으면 다음 수임처가 전부 죽는다."""
    from kafa.fetch.wehago import run_fetch

    class _Closable(_NamedPage):
        closed = False

        def __init__(self, name):
            super().__init__(name)

        def close(self):
            type(self).closed = True

    page = _Closable("가")
    cfg = {**_CFG_VERIFY, "close_ledger_after": True}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None,
                    download=False)
    assert res.ok and not _Closable.closed


def test_last_tab_is_kept_even_in_list_mode(tmp_path):
    """목록에서 열었더라도 남은 탭이 그것뿐이면 닫지 않는다."""
    from kafa.fetch.wehago import fetch_one

    class _Only(_NamedPage):
        closed = False

        def evaluate(self, js, arg=None):
            return "ok"

        def close(self):
            type(self).closed = True

    page = _Only("가")
    page.context = None                  # 다른 탭이 없다
    fetch_one(page, {**_CFG_VERIFY, "close_ledger_after": True},
              DownloadTask("가", "2026", cno="1"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert not _Only.closed


def test_pick_page_opens_a_new_tab_when_none_left():
    """탭이 다 닫혔으면 새로 연다 — 로그인 세션은 브라우저에 남아 있다."""
    from kafa.fetch.wehago import pick_page

    made = []

    class _Ctx:
        pages = []

        def new_page(self):
            made.append(True)
            return "새 탭"

    class _Dead:
        context = _Ctx()
        url = "about:blank"

        def is_closed(self):
            return True

    assert pick_page(_Dead(), {"selectors": {"search_button": "#go"}}) == "새 탭"
    assert made


def test_dropdown_is_closed_when_kind_selection_fails(tmp_path):
    """열린 목록이 조회 버튼을 덮어 다음 단계가 막히는 것을 방지한다."""
    from kafa.fetch.wehago import fetch_one

    class _Keyboard:
        def __init__(self):
            self.keys = []

        def press(self, key):
            self.keys.append(key)

    class _NoKind(_NamedPage):
        def __init__(self):
            super().__init__("가")
            self.keyboard = _Keyboard()

        def click(self, sel, **kw):
            if sel in ('text="1. 매출"', "#kindopen"):
                raise TimeoutError("목록이 안 열림")
            super().click(sel, **kw)

    cfg = {**_CFG_VERIFY, "kind_autoselect": True, "kind_current_other": "1. 매출",
           "kind_try_timeout_ms": 10,
           "selectors": {**_CFG_VERIFY["selectors"],
                         "kind_current": 'text="{kind}"',
                         "kind_select_open": ['text="{other}"', "#kindopen"],
                         "kind_option": 'li a:has-text("{kind}")'}}
    page = _NoKind()
    fetch_one(page, cfg, DownloadTask("가", "2026", url="https://x/a", cno="1"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    assert "Escape" in page.keyboard.keys      # 목록을 닫고 진행했다
    assert ("click", "#go") in page.log


# ── '조회조건에 맞는 데이터가 없습니다' 를 놓치지 않기 ──

def test_empty_detected_from_visible_dialog_text(tmp_path):
    """마침표·줄바꿈이 달라도 알림창 안의 문구로 잡는다."""
    from kafa.fetch.wehago import run_fetch

    class _Dialog(_NamedPage):
        searched = False

        def click(self, sel, **kw):
            if sel == "#go":
                type(self).searched = True      # 조회 후에야 알림창이 뜬다
            super().click(sel, **kw)

        def evaluate(self, js, arg=None):
            if "checkVisibility" in js:
                return ("조회조건에 맞는 데이터가 없습니다."
                        if type(self).searched else "")   # 마침표가 붙어 있다
            return "ok"

    cfg = {**_CFG_VERIFY, "empty_result_texts": ["조회조건에 맞는 데이터"],
           "empty_wait_seconds": 0.1, "task_retries": 0,
           "selectors": {**_CFG_VERIFY["selectors"], "popup_confirm": ["#ok"]}}
    page = _Dialog("가")
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None,
                    download=False)
    assert res.probed["가/2026"] == "자료 없음"
    assert ("click", "#ok") in page.log            # 팝업을 닫았다


def test_hidden_dialog_is_not_treated_as_empty(tmp_path):
    """숨어 있는 알림창(이전 것)은 자료 없음으로 보지 않는다."""
    from kafa.fetch.wehago import _has_any_text

    class _Hidden:
        def evaluate(self, js, arg=None):
            return ""            # 보이는 알림창이 없다

        def query_selector(self, sel):
            return None

    assert _has_any_text(_Hidden(), ["데이터가 없습니다"], {}) == ""


def test_falls_back_to_partial_text_selector(tmp_path):
    """알림창을 못 찾으면 부분일치 text 선택자로 한 번 더 본다."""
    from kafa.fetch.wehago import _has_any_text

    class _NoJs:
        def evaluate(self, js, arg=None):
            raise RuntimeError("no js")

        def query_selector(self, sel):
            # 숨은 글자를 잡지 않도록 :visible 이 붙어야 한다
            return object() if sel == "text=데이터가 없습니다:visible" else None

    assert _has_any_text(_NoJs(), ["데이터가 없습니다"], {}) == "데이터가 없습니다"


def test_empty_matching_ignores_spacing(tmp_path):
    """화면은 '조회 조건', 설정은 '조회조건' — 띄어쓰기 차이를 무시한다."""
    from kafa.fetch.wehago import _has_any_text

    real = ("조회 조건에 맞는 데이터가 없습니다. "
            "메뉴 상단 [수집하러가기] 버튼을 클릭하여 자동 전표를 수집하거나 "
            "수집한 기간에 맞춰 조회 조건을 다시 설정해 주시기 바랍니다.")

    class _Dialog:
        def evaluate(self, js, arg=None):
            _selectors, words = arg
            def squash(x):
                return "".join(str(x).split())
            keys = [squash(w) for w in words if w]
            return real if any(k in squash(real) for k in keys) else ""

        def query_selector(self, sel):
            return None

    found = _has_any_text(_Dialog(), ["조회조건에맞는데이터가없"], {})
    assert found.startswith("조회 조건에 맞는 데이터가 없습니다")


def test_shipped_empty_texts_match_the_real_popup():
    """배포 설정의 문구가 실제 팝업(띄어쓰기 포함)과 맞아야 한다."""
    from kafa.fetch.wehago import load_fetch_config
    real = "조회 조건에 맞는 데이터가 없습니다. 메뉴 상단 [수집하러가기] 버튼을"
    squashed = "".join(real.split())
    words = load_fetch_config()["empty_result_texts"]
    assert any("".join(str(w).split()) in squashed for w in words)


def test_static_notice_is_not_mistaken_for_the_popup(tmp_path):
    """조회 전부터 떠 있던 같은 문구는 팝업이 아니다(자료 있는 곳이 '없음' 이 되던 문제)."""
    from kafa.fetch.wehago import run_fetch

    class _Notice(_NamedPage):
        """화면에 늘 같은 안내문이 보이는 수임처."""
        def evaluate(self, js, arg=None):
            if "checkVisibility" in js:
                return "조회 조건에 맞는 데이터가 없습니다. 메뉴 상단 …"
            return "ok"

    cfg = {**_CFG_VERIFY, "empty_result_texts": ["조회조건에맞는데이터가없"],
           "empty_wait_seconds": 0.05, "task_retries": 0}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(_Notice("가"), plan, tmp_path, cfg=cfg, sleep=lambda _: None,
                    download=False)
    assert res.probed["가/2026"] == "자료 있음"


# ── 전환 실패 시 물러서는 순서: 새로고침 → 목록 ──

def test_reload_recovers_switch_before_touching_the_list(tmp_path):
    """해시만 바뀌어 화면이 그대로면, 새로고침만으로 대개 해결된다."""
    from kafa.fetch.wehago import fetch_one

    class _NeedsReload(_NamedPage):
        def __init__(self):
            super().__init__("(주)이전상사")
            self.reloaded = False
            self.js = []

        def reload(self, **kw):
            self.reloaded = True
            self.screen_name = "(주)행복상사"

        def evaluate(self, js, arg=None):
            self.js.append(arg)
            return "ok"

    page = _NeedsReload()
    dest = tmp_path / "행복상사" / "2026.xlsx"
    fetch_one(page, _CFG_VERIFY,
              DownloadTask("(주)행복상사", "2026", url="https://x/a", cno="9"),
              dest, resolve=lambda: page, sleep=lambda _s: None)
    assert page.reloaded and dest.exists()
    # 목록까지 가지 않았다(목록 열기는 [코드, 이름, 라벨] 3개짜리 호출)
    assert not [a for a in page.js if isinstance(a, list) and len(a) == 3]


def test_goes_to_dashboard_when_no_list_tab(tmp_path):
    """주소로만 다녀서 목록 탭이 없으면, 대시보드 주소로 먼저 간다."""
    from kafa.fetch.wehago import fetch_one

    class _NoList(_NamedPage):
        def __init__(self):
            super().__init__("(주)이전상사")
            self.present.discard("#s")      # 검색창이 없다
            self.js = []

        def reload(self, **kw):
            pass                            # 새로고침해도 그대로

        def goto(self, url, **kw):
            super().goto(url, **kw)
            if url == "https://home/":
                self.present.add("#s")      # 대시보드로 가면 검색창이 생긴다

        def evaluate(self, js, arg=None):
            self.js.append(arg)
            self.screen_name = "(주)행복상사"
            return "ok"

    cfg = {**_CFG_VERIFY, "dashboard_url": "https://home/"}
    page = _NoList()
    dest = tmp_path / "행복상사" / "2026.xlsx"
    fetch_one(page, cfg,
              DownloadTask("(주)행복상사", "2026", url="https://x/a", cno="9"),
              dest, resolve=lambda: page, sleep=lambda _s: None)
    assert "https://home/" in page.real_gotos
    assert page.js and page.js[0] == ["9", "(주)행복상사", "회계"]
    assert dest.exists()


def test_shipped_config_has_dashboard_url():
    from kafa.fetch.wehago import load_fetch_config
    assert str(load_fetch_config().get("dashboard_url", "")).startswith("http")


def test_sticks_to_the_same_tab_across_clients(tmp_path):
    """탭을 닫지 않으니 smarta 탭이 여러 개 남는다 — 쓰던 탭을 계속 써야 한다."""
    from kafa.fetch.wehago import run_fetch

    used = _NamedPage("가")
    stray = _NamedPage("남의회사")          # 다른 수임처가 열려 있는 탭

    class _Ctx:
        pages = [stray, used]              # 목록 순서상 stray 가 먼저

    used.context = _Ctx()
    stray.context = _Ctx()

    def _name_for(url):
        return {"https://x/1": "가", "https://x/2": "나"}[url]

    orig_goto = used.goto

    def _goto(url, **kw):
        orig_goto(url, **kw)
        if url != "about:blank":
            used.screen_name = _name_for(url)

    used.goto = _goto
    plan = build_plan(tmp_path, ["가", "나"], ["2026"],
                      urls={"가": "https://x/1", "나": "https://x/2"})
    res = run_fetch(used, plan, tmp_path, cfg=_CFG_VERIFY, sleep=lambda _: None,
                    download=False)
    assert res.ok and res.probed == {"가/2026": "자료 있음", "나/2026": "자료 있음"}
    assert stray.real_gotos == []          # 엉뚱한 탭은 건드리지 않았다


def test_leftover_popup_is_dismissed_before_search(tmp_path):
    """앞 수임처의 알림창이 남아 있으면 그 글자가 '원래 있던 것' 으로 잡혀
    자료가 없는데도 다운로드로 넘어갔다. 조회 전에 닫아 둔다."""
    from kafa.fetch.wehago import run_fetch

    class _Sticky(_NamedPage):
        """알림창이 한 번 뜨면 '확인' 을 눌러야 사라진다."""
        def __init__(self, name):
            super().__init__(name)
            self.popup = True          # 앞 건에서 남은 알림창
            self.searched = False

        def click(self, sel, **kw):
            if sel == "#ok":
                self.popup = False
            if sel == "#go":
                self.searched = True
                self.popup = True      # 조회 결과가 없어 다시 뜬다
            super().click(sel, **kw)

        def evaluate(self, js, arg=None):
            if "checkVisibility" in js:
                return "조회 조건에 맞는 데이터가 없습니다." if self.popup else ""
            return "ok"

    cfg = {**_CFG_VERIFY, "empty_result_texts": ["조회조건에맞는데이터가없"],
           "empty_wait_seconds": 0.05, "task_retries": 0,
           "selectors": {**_CFG_VERIFY["selectors"], "popup_confirm": ["#ok"]}}
    page = _Sticky("가")
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None,
                    download=False)
    assert res.probed["가/2026"] == "자료 없음"
    assert not [e for e in page.log if e[1] == "#xls"]   # 다운로드로 안 갔다


def test_download_failure_with_notice_counts_as_no_data(tmp_path):
    """못 받았는데 안내문이 떠 있으면 '실패' 가 아니라 '자료 없음' 이다.

    글자로 미리 재는 것보다 **실제로 못 받았다** 는 사실이 확실한 근거다.
    """
    from kafa.fetch.wehago import run_fetch

    class _NoFile(_NamedPage):
        def __init__(self, name):
            super().__init__(name)
            self.searched = False

        def click(self, sel, **kw):
            if sel == "#go":
                self.searched = True
            if sel == "#xls":
                raise TimeoutError("파일이 안 만들어짐")
            super().click(sel, **kw)

        def evaluate(self, js, arg=None):
            if "checkVisibility" in js:
                # 조회 후에만 안내문이 보인다(조회 전 판정은 통과시킨다)
                return "조회 조건에 맞는 데이터가 없습니다." if self.searched else ""
            return "ok"

    cfg = {**_CFG_VERIFY, "empty_result_texts": ["조회조건에맞는데이터가없"],
           "empty_wait_seconds": 0, "task_retries": 0, "menu_retries": 1,
           "selectors": {**_CFG_VERIFY["selectors"], "popup_confirm": ["#ok"]}}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(_NoFile("가"), plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and res.empty == ["가/2026"] and res.failures == {}


def test_download_failure_without_notice_stays_a_failure(tmp_path):
    """안내문이 없는데 못 받았으면 그건 진짜 실패다(조용히 넘기지 않는다)."""
    from kafa.fetch.wehago import run_fetch

    class _Broken(_NamedPage):
        def click(self, sel, **kw):
            if sel == "#xls":
                raise TimeoutError("메뉴가 안 뜸")
            super().click(sel, **kw)

        def evaluate(self, js, arg=None):
            return "" if "checkVisibility" in js else "ok"

    cfg = {**_CFG_VERIFY, "empty_result_texts": ["조회조건에맞는데이터가없"],
           "empty_wait_seconds": 0, "task_retries": 0, "menu_retries": 1}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(_Broken("가"), plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert not res.ok and "가/2026" in res.failures


# ── 화면의 건수로 판정 (문구보다 숫자가 안 흔들린다) ──

_CFG_COUNT = {**_CFG_VERIFY,
              "result_count": {"pattern": r"미처리\s*전표\s*건\s*수\s*([0-9,]+)",
                               "selectors": ["body"], "wait_seconds": 0.05,
                               "decide": True}}


class _CountPage(_NamedPage):
    """조회하면 화면에 '미처리 전표 건 수 N 건' 이 뜬다."""
    def __init__(self, name, count):
        super().__init__(name)
        self.count, self.searched = count, False

    def click(self, sel, **kw):
        if sel == "#go":
            self.searched = True
        super().click(sel, **kw)

    def evaluate(self, js, arg=None):
        if "innerText" in js and "checkVisibility" in js:
            return (f"미전송현황 미처리 전표 건 수 {self.count} 건"
                    if self.searched else "")
        if "checkVisibility" in js:
            return ""
        return "ok"


def test_zero_count_is_no_data(tmp_path):
    from kafa.fetch.wehago import run_fetch
    page = _CountPage("가", 0)
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_COUNT, sleep=lambda _: None)
    assert res.ok and res.empty == ["가/2026"]
    assert not [e for e in page.log if e[1] == "#xls"]   # 다운로드로 안 갔다


def test_positive_count_proceeds_to_download(tmp_path):
    from kafa.fetch.wehago import run_fetch
    page = _CountPage("가", "1,234")
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_COUNT, sleep=lambda _: None)
    assert res.ok and len(res.saved) == 1


def test_missing_count_falls_back_to_notice(tmp_path):
    """건수를 못 읽으면 예전처럼 문구로 판정한다."""
    from kafa.fetch.wehago import result_count

    class _NoCount:
        def evaluate(self, js, arg=None):
            return "그런 표시 없음"

    assert result_count(_NoCount(), _CFG_COUNT) is None


def test_count_pattern_reads_the_real_screen_text():
    from kafa.fetch.wehago import load_fetch_config, result_count

    real = "안내 미전송현황 미처리 전표 건 수 401 건 녹색 매출 …"

    class _Real:
        def evaluate(self, js, arg=None):
            return real

    assert result_count(_Real(), load_fetch_config()) == 401


# ── 표가 안 그려지면 자료 없음 (자료가 없으면 canvas 가 생기지 않는다) ──

def test_missing_result_grid_is_no_data(tmp_path):
    from kafa.fetch.wehago import run_fetch

    class _NoGrid(_NamedPage):
        def __init__(self, name):
            super().__init__(name)
            self.present.discard("div#GRID_TOP canvas")   # 표가 안 생긴다

        def evaluate(self, js, arg=None):
            return "" if "checkVisibility" in js else "ok"

    cfg = {**_CFG_VERIFY, "empty_when_missing": ["div#GRID_TOP canvas"],
           "grid_wait_seconds": 0.05, "empty_wait_seconds": 0, "task_retries": 0}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    page = _NoGrid("가")
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and res.empty == ["가/2026"] and res.failures == {}
    # 우클릭·엑셀 클릭을 아예 시도하지 않는다(3번 재시도로 시간 버리지 않게)
    assert not [e for e in page.log if e[1] == "#xls"]


def test_present_grid_proceeds(tmp_path):
    from kafa.fetch.wehago import run_fetch
    cfg = {**_CFG_VERIFY, "empty_when_missing": ["div#GRID_TOP canvas"],
           "grid_wait_seconds": 0.05, "empty_wait_seconds": 0, "task_retries": 0}
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(_NamedPage("가"), plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and len(res.saved) == 1


def test_shipped_config_marks_empty_by_missing_grid():
    from kafa.fetch.wehago import _as_list, load_fetch_config
    assert "div#GRID_TOP canvas" in _as_list(
        load_fetch_config().get("empty_when_missing"))


# ── 표의 '데이터 행' 에서 우클릭해야 메뉴가 뜬다 ──

class _GridPage(_NamedPage):
    """지정한 위치에서만 메뉴가 열리는 표(그 밖은 빈 공간)."""
    def __init__(self, name, hot=(120, 48)):
        super().__init__(name)
        self.hot, self.menu, self.clicks = hot, False, []

    def click(self, sel, **kw):
        pos = kw.get("position")
        self.clicks.append((sel, kw.get("button", "left"), pos))
        if sel == "div#GRID_TOP canvas" and kw.get("button") == "right":
            self.menu = pos is not None and (pos["x"], pos["y"]) == self.hot
            if not self.menu:
                return                      # 빈 자리 — 메뉴가 안 뜬다
        if sel == "#xls" and not self.menu:
            raise TimeoutError("메뉴가 없다")
        super().click(sel, **kw)

    def query_selector(self, sel):
        if sel == "#xls":
            return object() if self.menu else None
        return super().query_selector(sel)


_CFG_GRID = {**_CFG_VERIFY, "menu_open_seconds": 0, "menu_retry_seconds": 0,
             "menu_retries": 1, "empty_wait_seconds": 0,
             "context_click_positions": [{"x": 120, "y": 24}, {"x": 120, "y": 48}]}


def test_tries_several_spots_until_the_menu_opens(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _GridPage("가", hot=(120, 48))      # 첫 자리는 빈 공간
    dest = tmp_path / "가" / "2026.xlsx"
    fetch_one(page, _CFG_GRID, DownloadTask("가", "2026", url="https://x/a"),
              dest, resolve=lambda: page, sleep=lambda _s: None)
    rights = [c for c in page.clicks if c[1] == "right"]
    assert [(c[2]["x"], c[2]["y"]) for c in rights] == [(120, 24), (120, 48)]
    assert dest.exists()


def test_selects_row_with_left_click_before_right_click(tmp_path):
    """기록된 사람 동작도 좌클릭으로 행을 고른 뒤 우클릭이었다."""
    from kafa.fetch.wehago import fetch_one
    page = _GridPage("가", hot=(120, 24))
    fetch_one(page, _CFG_GRID, DownloadTask("가", "2026", url="https://x/a"),
              tmp_path / "가" / "2026.xlsx", resolve=lambda: page,
              sleep=lambda _s: None)
    grid = [c for c in page.clicks if c[0] == "div#GRID_TOP canvas"]
    assert grid[0][1] == "left" and grid[1][1] == "right"
    assert grid[0][2] == grid[1][2]          # 같은 자리를 짚는다


def test_shipped_config_has_context_click_positions():
    from kafa.fetch.wehago import load_fetch_config
    spots = load_fetch_config().get("context_click_positions") or []
    assert len(spots) >= 2 and all("x" in s and "y" in s for s in spots)


# ── 컬럼 바로 아래부터 훑어 내려가며 행을 찾는다 ──

def test_scan_starts_near_the_header_and_goes_down():
    from kafa.fetch.wehago import context_spots
    spots = context_spots({"context_click_positions": [{"x": 120, "y": 44}],
                           "context_click_scan": {"x": 120, "y_start": 32,
                                                  "y_step": 10, "y_max": 62}})
    assert [(s["x"], s["y"]) for s in spots] == [
        (120, 44), (120, 32), (120, 42), (120, 52), (120, 62)]


def test_scan_drops_duplicate_spots():
    from kafa.fetch.wehago import context_spots
    spots = context_spots({"context_click_positions": [{"x": 1, "y": 10}],
                           "context_click_scan": {"x": 1, "y_start": 10,
                                                  "y_step": 10, "y_max": 20}})
    assert [(s["x"], s["y"]) for s in spots] == [(1, 10), (1, 20)]


def test_finds_the_row_even_when_only_one_line(tmp_path):
    """줄이 한 개뿐이면 아래쪽은 빈 공간 — 위쪽 어딘가를 찾아내야 한다."""
    from kafa.fetch.wehago import fetch_one
    page = _GridPage("가", hot=(120, 52))       # 첫 줄이 y=52 에 있다
    cfg = {**_CFG_GRID, "context_click_positions": [],
           "context_click_scan": {"x": 120, "y_start": 32, "y_step": 10,
                                  "y_max": 140}}
    dest = tmp_path / "가" / "2026.xlsx"
    fetch_one(page, cfg, DownloadTask("가", "2026", url="https://x/a"), dest,
              resolve=lambda: page, sleep=lambda _s: None)
    assert dest.exists()
    ys = [c[2]["y"] for c in page.clicks if c[1] == "right"]
    assert ys[:3] == [32, 42, 52]              # 위에서 아래로 훑었다


def test_shipped_config_scans_from_just_below_header():
    from kafa.fetch.wehago import context_spots, load_fetch_config
    spots = context_spots(load_fetch_config())
    assert len(spots) >= 8                     # 헤더 높이가 달라도 찾도록 촘촘히
    assert min(s["y"] for s in spots) <= 40     # 컬럼 바로 아래부터


def test_count_zero_does_not_decide_by_default(tmp_path):
    """그 숫자는 매출·매입이 섞여 있어 그대로 믿으면 안 된다(기록만)."""
    from kafa.fetch.wehago import run_fetch
    cfg = {**_CFG_COUNT,
           "result_count": {**_CFG_COUNT["result_count"], "decide": False}}
    page = _CountPage("가", 0)
    plan = build_plan(tmp_path, ["가"], ["2026"], urls={"가": "https://x/a"})
    res = run_fetch(page, plan, tmp_path, cfg=cfg, sleep=lambda _: None)
    assert res.ok and len(res.saved) == 1 and res.empty == []


def test_shipped_config_stops_on_zero_count():
    """건수 0이면 받지 않는다 — 덜 받는 것보다 남의 자료가 섞이는 게 훨씬 나쁘다."""
    from kafa.fetch.wehago import load_fetch_config
    assert (load_fetch_config().get("result_count") or {}).get("decide") is True


def test_client_is_verified_again_right_before_download(tmp_path):
    """조회 결과가 없을 때 앞 수임처의 표가 남아 있으면 그걸 받게 된다 — 직전에 재확인."""
    from kafa.fetch.wehago import WrongClient, fetch_one

    class _DriftsAway(_NamedPage):
        """조회까지는 맞다가, 조회 뒤 화면이 다른 수임처 것으로 남아 있다."""
        def __init__(self):
            super().__init__("(주)행복상사")
            self.searched = False

        def click(self, sel, **kw):
            if sel == "#go":
                self.searched = True
                self.screen_name = "(주)남의회사"
            super().click(sel, **kw)

        def evaluate(self, js, arg=None):
            return "" if "checkVisibility" in js else "ok"

    dest = tmp_path / "행복상사" / "2026.xlsx"
    page = _DriftsAway()
    cfg = {**_CFG_VERIFY, "empty_wait_seconds": 0, "verify_timeout_ms": 20,
           "menu_open_seconds": 0, "context_click_positions": [{"x": 1, "y": 1}],
           "context_click_scan": {}}
    try:
        fetch_one(page, cfg,
                  DownloadTask("(주)행복상사", "2026", url="https://x/a"),
                  dest, resolve=lambda: page, sleep=lambda _s: None)
    except WrongClient:
        assert not dest.exists()           # 남의 자료를 저장하지 않았다
    else:
        raise AssertionError("WrongClient 가 나와야 한다")


# ── 매출건수·매입건수가 따로 있다 — 매입 숫자를 집어야 한다 ──

def test_picks_purchase_count_not_the_total():
    from kafa.fetch.wehago import load_fetch_config, result_count

    class _Screen:
        def __init__(self, text):
            self.text = text

        def evaluate(self, js, arg=None):
            return self.text

    cfg = load_fetch_config()
    총합만 = "미전송현황 미처리 전표 건 수 401 건"
    갈라짐 = "미처리 전표 건 수 401 건 매출건수 0 건 매입건수 401 건"
    매출만 = "미처리 전표 건 수 5 건 매출건수 5 건 매입건수 0 건"

    assert result_count(_Screen(총합만), cfg) == 401      # 갈라져 있지 않으면 합계
    assert result_count(_Screen(갈라짐), cfg) == 401      # 매입 숫자
    assert result_count(_Screen(매출만), cfg) == 0        # 매입이 0이면 0


def test_count_context_is_logged_for_tuning():
    from kafa.fetch.wehago import count_context, load_fetch_config
    spec = load_fetch_config()["result_count"]
    ctx = count_context("앞부분 미처리 전표 건 수 401 건 매입건수 401 건 뒷부분", spec)
    assert "미처리" in ctx and "매입건수" in ctx
