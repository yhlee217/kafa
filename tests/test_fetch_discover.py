"""수임처 목록·접속 URL 을 화면에서 직접 모으기. 브라우저 없이 검증."""
from kafa.clients import client_urls, client_urls_from_csv
from kafa.fetch.discover import (collect_clients, find_url_template, merge,
                                 url_template, write_csv)


class _Tab:
    def __init__(self, url, rows=None):
        self.url, self._rows = url, rows or []
        self.context = None

    def is_closed(self):
        return False

    def evaluate(self, _js, _arg=None):
        return self._rows


class _Ctx:
    def __init__(self, pages):
        self.pages = pages


def test_url_template_replaces_only_cno():
    got = url_template("https://smarta.wehago.com/#/smarta/account/"
                       "SAAC0105?sao&cno=10049328&cd=7")
    assert got == ("https://smarta.wehago.com/#/smarta/account/"
                   "SAAC0105?sao&cno={cno}&cd=7")


def test_url_template_empty_without_cno():
    assert url_template("https://www.wehago.com/#/main") == ""


def test_find_template_across_tabs():
    main = _Tab("https://www.wehago.com/#/main")
    card = _Tab("https://smarta.wehago.com/#/x?cno=123")
    main.context = _Ctx([main, card])
    assert find_url_template(main) == "https://smarta.wehago.com/#/x?cno={cno}"


def test_collect_and_merge_dedupes_across_pages():
    tab = _Tab("https://x/", rows=[{"cno": "1", "name": "가"},
                                   {"cno": "2", "name": "나"}])
    tab.context = _Ctx([tab])
    known: dict = {}
    assert merge(known, collect_clients(tab)) == 2
    # 같은 페이지를 또 읽어도 늘지 않는다
    assert merge(known, collect_clients(tab)) == 0
    tab._rows = [{"cno": "2", "name": "나"}, {"cno": "3", "name": "다"}]
    assert merge(known, collect_clients(tab)) == 1


def test_written_csv_carries_client_code_not_url(tmp_path):
    """주소는 수임처마다 다른 값이 섞여 있어 만들지 않는다 — 코드만 남긴다."""
    from kafa.clients import client_cnos
    out = write_csv(tmp_path / "clients_urls.csv", {"10049328": "행복상사"})
    assert client_cnos(out) == {"행복상사": "10049328"}
    assert client_urls_from_csv(out) == {}    # URL 칸 없음


def test_written_csv_with_template_still_feeds_url_mode(tmp_path):
    out = write_csv(tmp_path / "u.csv", {"10049328": "행복상사"},
                    "https://smarta.wehago.com/#/x?cno={cno}")
    urls = client_urls_from_csv(out)
    assert urls == {"행복상사": "https://smarta.wehago.com/#/x?cno=10049328"}
    assert client_urls(out) == urls          # --master 가 .csv 도 받는다


def test_collect_ignores_tabs_without_list():
    class _Bad(_Tab):
        def evaluate(self, _js, _arg=None):
            raise RuntimeError("no list")

    bad = _Bad("https://x/")
    good = _Tab("https://y/", rows=[{"cno": "9", "name": "다"}])
    bad.context = _Ctx([bad, good])
    assert collect_clients(bad) == [{"cno": "9", "name": "다"}]


def test_expected_total_reads_screen_count():
    from kafa.fetch.discover import expected_total

    class _T(_Tab):
        def evaluate(self, js, _arg=None):
            if "innerText" in js:
                return "담당 수임처136 수임처관리 노란우산공제"
            return []

    tab = _T("https://x/")
    tab.context = _Ctx([tab])
    assert expected_total(tab) == 136


def test_expected_total_zero_when_absent():
    from kafa.fetch.discover import expected_total

    class _T(_Tab):
        def evaluate(self, js, _arg=None):
            return "그런 표시 없음" if "innerText" in js else []

    tab = _T("https://x/")
    tab.context = _Ctx([tab])
    assert expected_total(tab) == 0


# ── 자동 넘기기 ──

class _Paged:
    """쪽번호 버튼을 누르면 다음 묶음이 나오는 가짜 목록 화면."""
    def __init__(self, pages, total_text="담당 수임처4"):
        self.pages, self.i, self.clicks = pages, 0, []
        self.total_text = total_text
        self.context = None
        self.url = "https://x/main"

    def is_closed(self):
        return False

    def evaluate(self, js, arg=None):
        if "innerText" in js:
            return self.total_text
        if "scrollTop" in js:
            return False
        return self.pages[self.i]

    def click(self, sel, **kw):
        self.clicks.append(sel)
        if sel.startswith("button:text-is") and self.i + 1 < len(self.pages):
            self.i += 1
            return
        raise TimeoutError("없음")


_CFG_D = {"discover": {"max_pages": 10, "wait_seconds": 0, "click_timeout_ms": 10,
                       "page_number_button": ['button:text-is("{n}")'],
                       "next_buttons": [], "scroll": True}}


def test_auto_collect_pages_through_until_total():
    from kafa.fetch.discover import auto_collect
    pg = _Paged([[{"cno": "1", "name": "가"}, {"cno": "2", "name": "나"}],
                 [{"cno": "3", "name": "다"}, {"cno": "4", "name": "라"}]])
    pg.context = _Ctx([pg])
    known = auto_collect(pg, _CFG_D, sleep=lambda _s: None)
    assert len(known) == 4
    assert 'button:text-is("2")' in pg.clicks


def test_auto_collect_stops_when_nothing_new():
    from kafa.fetch.discover import auto_collect
    pg = _Paged([[{"cno": "1", "name": "가"}]], total_text="표시 없음")
    pg.context = _Ctx([pg])
    events = []
    known = auto_collect(pg, _CFG_D, on_event=events.append, sleep=lambda _s: None)
    assert len(known) == 1
    assert any("멈춥니다" in e for e in events)


def test_auto_collect_reports_counts_only():
    """진행 출력에 수임처 이름이 새어나가면 안 된다(보안 제0원칙)."""
    from kafa.fetch.discover import auto_collect
    pg = _Paged([[{"cno": "1", "name": "비밀상사"}]], total_text="표시 없음")
    pg.context = _Ctx([pg])
    events = []
    auto_collect(pg, _CFG_D, on_event=events.append, sleep=lambda _s: None)
    assert not any("비밀상사" in e for e in events)


def test_switches_strategy_when_page_button_does_nothing():
    """쪽번호를 눌러도 목록이 그대로면 다른 방법으로 바꾼다."""
    from kafa.fetch.discover import auto_collect

    class _FakePager(_Paged):
        """쪽번호 클릭은 '성공'하지만 목록은 그대로. 스크롤에서만 늘어난다."""
        def __init__(self):
            super().__init__([[{"cno": "1", "name": "가"}]], total_text="담당 수임처2")
            self.scrolled = False

        def evaluate(self, js, arg=None):
            if "innerText" in js:
                return self.total_text
            if "scrollTop" in js:
                self.scrolled = True
                self.pages = [[{"cno": "1", "name": "가"},
                               {"cno": "2", "name": "나"}]]
                return True
            return self.pages[0]

        def click(self, sel, **kw):
            self.clicks.append(sel)      # 눌리기는 하지만 아무 일도 안 일어남

    pg = _FakePager()
    pg.context = _Ctx([pg])
    known = auto_collect(pg, _CFG_D, sleep=lambda _s: None)
    assert pg.scrolled and len(known) == 2


def test_pagination_report_lists_candidates():
    from kafa.fetch.discover import pagination_report

    class _T(_Tab):
        def evaluate(self, js, _arg=None):
            if "words" in js:
                return [{"tag": "button", "text": "2", "attrs": 'class="pg"',
                         "vis": True, "box": "10,20"}]
            return []

    tab = _T("https://x/")
    tab.context = _Ctx([tab])
    out = "\n".join(pagination_report(tab))
    assert "<button> '2'" in out and 'class="pg"' in out
