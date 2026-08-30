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


def test_written_csv_feeds_master_option(tmp_path):
    out = write_csv(tmp_path / "clients_urls.csv", {"10049328": "행복상사"},
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
