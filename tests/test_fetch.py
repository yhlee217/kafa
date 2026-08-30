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


def test_shipped_config_still_blocks_unverified_paths():
    """확인하지 못한 경로(화면 검색·달력 월지정)는 여전히 실행을 막는다."""
    from kafa.fetch.wehago import load_fetch_config
    cfg = load_fetch_config()
    assert missing_selectors(cfg, url_mode=False)           # 화면 검색 미보정
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
                      "period_from_input": "#f", "period_to_input": "#t",
                      "search_button": "#go", "excel_download_button": "#xls"},
        "delay_seconds": 0, "timeout_ms": 100, "period_format": "%Y-%m"}


class _FakeDownload:
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
    def __init__(self, fail_on=None):
        self.log, self.fail_on = [], fail_on or set()
    def fill(self, sel, val, **kw):
        if val in self.fail_on:
            raise RuntimeError(f"입력 실패: {val}")
        self.log.append(("fill", sel, val))
    def click(self, sel, **kw): self.log.append(("click", sel))
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
            "delay_seconds": 0, "timeout_ms": 100, "period_format": "%Y-%m"}


class _UrlPage(_FakePage):
    """goto 를 기록하고, login_hits 만큼 로그인 화면을 흉내낸다."""
    def __init__(self, login_hits=0):
        super().__init__()
        self.goto_urls = []
        self.login_hits = login_hits
    def goto(self, url, **kw):
        self.goto_urls.append(url)
    def query_selector(self, sel):
        if sel == "#login" and self.login_hits > 0:
            self.login_hits -= 1
            return object()          # 로그인 화면 감지
        return None


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
               "delay_seconds": 0, "timeout_ms": 100}


def test_screen_mode_skips_calendar_and_picks_purchase(tmp_path):
    from kafa.fetch.wehago import fetch_one
    page = _UrlPage()
    task = DownloadTask("행복상사", "2026", url="https://x/1")
    fetch_one(page, _CFG_SCREEN, task, tmp_path / "행복상사" / "2026.xlsx")
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
