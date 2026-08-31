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
        "menu_retry_seconds": 0}


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
        "menu_retry_seconds": 0}


class _UrlPage(_FakePage):
    """goto 를 기록하고, login_hits 만큼 로그인 화면을 흉내낸다."""
    def __init__(self, login_hits=0, present=()):
        super().__init__(present=present)
        self.goto_urls = []
        self.login_hits = login_hits
    def goto(self, url, **kw):
        self.goto_urls.append(url)
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
    assert res.ok and page.goto_urls == ["https://x/acct?cno=1"]
    assert ("fill", "#s", "행복상사") not in page.log      # 검색창 안 씀


def test_session_expiry_asks_human_then_retries(tmp_path):
    page = _UrlPage(login_hits=1)          # 첫 시도에 로그인 화면
    asked = []
    plan = build_plan(tmp_path, ["행복상사"], ["2026-01"],
                      urls={"행복상사": "https://x/1"})
    res = run_fetch(page, plan, tmp_path, cfg=_CFG_URL, sleep=lambda _: None,
                    on_session_expired=lambda: asked.append(True))
    assert asked == [True] and res.ok       # 사람이 재로그인 후 성공
    assert len(page.goto_urls) == 2         # 재시도


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
               "menu_retry_seconds": 0}


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
        def query_selector(self, sel):
            if sel == 'text="조회된 자료가 없습니다"':
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
           "menu_retries": 1}
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
        def query_selector(self, sel):
            if sel == 'text="조회조건에 맞는 데이터가 없습니다"':
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
