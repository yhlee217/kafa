"""동작 기록 — 사람이 한 번 받는 동안의 클릭 순서를 잡는다. 브라우저 없이 검증."""
from kafa.fetch.record import format_steps, record_flow


class _Frame:
    """drain 될 때마다 다음 묶음을 돌려주는 가짜 프레임."""
    def __init__(self, batches):
        self.batches, self.installed = list(batches), 0

    def evaluate(self, js, _arg=None):
        if "__kafa_rec = []" in js and "addEventListener" in js:
            self.installed += 1
            return "installed" if self.installed == 1 else "already"
        return self.batches.pop(0) if self.batches else []


class _Page:
    def __init__(self, frame, url="https://x/card", title="신용카드"):
        self.frames = [frame]
        self.url = url
        self._title = title
        self.context = None
        self.handlers = {}

    def is_closed(self):
        return False

    def title(self):
        return self._title

    def on(self, event, cb):
        self.handlers[event] = cb


class _Dl:
    suggested_filename = "신용카드.xlsx"


def _click(path, text="", attrs=""):
    return {"type": "click", "path": path, "tag": "button", "text": text,
            "attrs": attrs, "value": ""}


def test_records_clicks_in_order():
    fr = _Frame([[_click("button#excel", "엑셀")],
                 [_click("a.menu", "조회한 데이터를 엑셀로 변환")]])
    pg = _Page(fr)
    out = record_flow(pg, seconds=10, interval=0, stop_after_download=False,
                      sleep=lambda _s: None, now=iter([0, 1, 2, 20]).__next__)
    joined = "\n".join(out)
    assert " 1. [클릭] button#excel" in joined
    assert " 2. [클릭] a.menu" in joined
    assert joined.index("button#excel") < joined.index("a.menu")


def test_marks_step_that_triggered_download():
    fr = _Frame([[_click("button#excel", "엑셀")],
                 [_click("a.menu", "엑셀로 변환")]])
    pg = _Page(fr)
    ticks = iter([0, 1, 2, 3, 4, 5, 6, 7, 20])

    fired = []

    def _sleep(_s):
        # 두 번째 배치를 읽은 뒤 다운로드가 한 번 발생한 것으로 흉내
        if fr.batches == [] and "download" in pg.handlers and not fired:
            fired.append(True)
            pg.handlers["download"](_Dl())

    out = "\n".join(record_flow(pg, seconds=30, interval=0, sleep=_sleep,
                                now=ticks.__next__))
    assert "파일이 내려받아졌습니다" in out
    assert "[다운로드] 1건 감지: 신용카드.xlsx" in out


def test_masks_typed_values_but_keeps_dates():
    steps = format_steps([
        {"type": "change", "path": "input#from", "tag": "input", "text": "",
         "attrs": "", "value": "2026.01.01"},
        {"type": "change", "path": "input#q", "tag": "input", "text": "",
         "attrs": "", "value": "풍무로솥뚜껑"},
    ])
    joined = "\n".join(steps)
    assert "값=2026.01.01" in joined
    assert "풍무로솥뚜껑" not in joined and "값=‹입력값›" in joined


def test_masks_company_name_in_text():
    joined = "\n".join(format_steps([_click("a#c", "(주) 어떤회사")]))
    assert "어떤회사" not in joined and "‹회사명›" in joined


def test_reports_when_nothing_recorded():
    joined = "\n".join(format_steps([]))
    assert "기록된 클릭이 없습니다" in joined
    assert "감지되지 않았습니다" in joined
