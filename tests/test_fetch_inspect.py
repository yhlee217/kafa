"""보정 도우미(--inspect) — 라벨/숨은텍스트 보강 + 엑셀 정밀 스캔.

브라우저 없이 가짜 프레임으로 검증. 값(value)은 읽지 않는지도 확인한다.
"""
from kafa.fetch.inspect import inspect_page


class _El:
    def __init__(self, text="", attrs="", ctx="", blind="", alt=""):
        self._text, self._info = text, {
            "attrs": attrs, "ctx": ctx, "blind": blind, "alt": alt}

    def inner_text(self):
        return self._text

    def evaluate(self, _js):
        return self._info


class _Frame:
    def __init__(self, elems=None, hits=None, url="https://x/acct"):
        self.elems, self.hits, self.url = elems or {}, hits or [], url

    def query_selector_all(self, css):
        return self.elems.get(css, [])

    def evaluate(self, _js, _arg=None):
        return self.hits


class _Page:
    def __init__(self, *frames, url="https://x/"):
        self.frames = list(frames)
        self.url = url
        self.context = None          # 단일 탭(테스트 더블)

    def is_closed(self):
        return False


class _Ctx:
    def __init__(self, pages):
        self.pages = pages


def test_icon_button_shows_hidden_label():
    """텍스트 없는 아이콘 버튼도 숨은텍스트/아이콘 alt 로 알아볼 수 있어야 한다."""
    fr = _Frame({"button:visible": [
        _El(text="", attrs='id="excel" class="WSC_LUXButton"', blind="엑셀다운로드")]})
    out = "\n".join(inspect_page(_Page(fr)))
    assert "숨은텍스트='엑셀다운로드'" in out
    assert 'id="excel"' in out


def test_input_shows_nearby_label():
    fr = _Frame({"input:visible": [
        _El(text="", attrs='id="dtFrom" type="text"', ctx="조회기간")]})
    out = "\n".join(inspect_page(_Page(fr)))
    assert "라벨='조회기간'" in out
    assert 'id="dtFrom"' in out


def test_deep_scan_reports_hidden_excel_button():
    """접힌 메뉴 안(숨음)이라도 엑셀 버튼 후보를 보여준다."""
    fr = _Frame(hits=[{"sel": "button#btnExcel.LUX", "text": "엑셀",
                       "attrs": 'id="btnExcel"', "vis": False}])
    out = "\n".join(inspect_page(_Page(fr)))
    assert "엑셀·다운로드 정밀 스캔" in out
    assert "[숨음] button#btnExcel.LUX" in out


def test_frames_are_labelled():
    main = _Frame({"button:visible": [_El(text="조회", attrs='id="go"')]}, url="https://x/")
    inner = _Frame({"button:visible": [_El(text="엑셀", attrs='id="xls"')]},
                   url="https://x/inner")
    out = "\n".join(inspect_page(_Page(main, inner)))
    assert "프레임 2개" in out
    assert "iframe#1: https://x/inner" in out


def test_scans_other_tabs():
    """회계 모듈이 새 탭으로 열려도 찾아낸다(첫 탭은 비어 있음)."""
    blank = _Page(_Frame(url="about:blank"), url="about:blank")
    real = _Page(_Frame({"button:visible": [_El(text="엑셀", attrs='id="xls"')]},
                        url="https://x/card"), url="https://x/card")
    blank.context = _Ctx([blank, real])
    out = "\n".join(inspect_page(blank))
    assert "탭 2개" in out
    assert "탭#1" in out and 'id="xls"' in out
    assert "요소를 찾지 못했습니다" not in out


def test_survives_broken_elements():
    class _Bad(_El):
        def inner_text(self):
            raise RuntimeError("detached")

        def evaluate(self, _js):
            raise RuntimeError("detached")

    class _BadFrame(_Frame):
        def evaluate(self, _js, _arg=None):
            raise RuntimeError("no js")

    out = inspect_page(_Page(_BadFrame({"button:visible": [_Bad()]})))
    assert "요소를 찾지 못했습니다" in "\n".join(out)


# ── CLI: 작업 뒤 브라우저를 바로 닫지 않는다 ──

def test_cli_inspect_keeps_browser_open_until_enter(tmp_path, monkeypatch):
    """살펴보기가 끝나도 컨텍스트를 빠져나가기 전에 사람에게 먼저 묻는다."""
    import contextlib

    from kafa.fetch import cli as fetch_cli
    from kafa.fetch import inspect as fetch_inspect
    from kafa.fetch import session as fetch_session

    order = []

    @contextlib.contextmanager
    def _fake_browser(**_kw):
        try:
            yield object()
        finally:
            order.append("closed")

    monkeypatch.setattr(fetch_session, "browser_page", _fake_browser)
    monkeypatch.setattr(fetch_session, "wait_for_human", lambda *a, **k: None)
    monkeypatch.setattr(fetch_inspect, "inspect_page", lambda _p: ["[화면 목록] 탭 1개"])

    out = tmp_path / "kafa-inspect.txt"
    answers = iter(["r", ""])          # 한 번 다시 살펴본 뒤 종료

    def _input(prompt=""):
        order.append("asked")
        return next(answers)

    rc = fetch_cli.main(["--inspect", "--inspect-out", str(out)], input_fn=_input)
    assert rc == 0
    assert out.read_text(encoding="utf-8").startswith("[화면 목록]")
    # 두 번 묻고(재시도 1회 포함) 그 다음에 닫혔다
    assert order == ["asked", "asked", "closed"]


def test_cli_inspect_no_keep_open_closes_immediately(tmp_path, monkeypatch):
    import contextlib

    from kafa.fetch import cli as fetch_cli
    from kafa.fetch import inspect as fetch_inspect
    from kafa.fetch import session as fetch_session

    order = []

    @contextlib.contextmanager
    def _fake_browser(**_kw):
        try:
            yield object()
        finally:
            order.append("closed")

    monkeypatch.setattr(fetch_session, "browser_page", _fake_browser)
    monkeypatch.setattr(fetch_session, "wait_for_human", lambda *a, **k: None)
    monkeypatch.setattr(fetch_inspect, "inspect_page", lambda _p: ["x"])

    def _input(prompt=""):
        order.append("asked")
        return ""

    rc = fetch_cli.main(["--inspect", "--no-keep-open",
                         "--inspect-out", str(tmp_path / "o.txt")], input_fn=_input)
    assert rc == 0 and order == ["closed"]


# ── 화면 판정 ──

def test_hint_detects_dashboard():
    out = inspect_page(_Page(_Frame({"button:visible": [
        _El(text="수집정보등록", attrs='class="button_bg_blue"')]})))
    joined = "\n".join(out)
    assert "수임처 목록(대시보드)" in joined
    assert "r + 엔터" in joined


def test_hint_detects_ledger_screen():
    out = inspect_page(_Page(_Frame({"button:visible": [
        _El(text="상세검색 열기", attrs='class="btnmore"'),
        _El(text="전표상태 안내", attrs='class="WSC_LUXButton"')]})))
    assert "회계 전표 화면으로 보입니다" in "\n".join(out)


def test_hint_unknown_screen():
    out = inspect_page(_Page(_Frame({"button:visible": [_El(text="확인")]})))
    assert "확신할 수 없습니다" in "\n".join(out)
