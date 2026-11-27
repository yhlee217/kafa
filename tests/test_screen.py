"""답지 화면(로컬 브라우저 프로그램) — 서버 API 왕복. 합성 데이터만."""
import json
import threading
import urllib.error
import urllib.request

import openpyxl
import pytest

from kafa.desk import Paths, read_sheet
from kafa.screen.server import make_server

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _todo(vendor, bizno, 업태, 종목, 일자, 합계):
    return ["2026", 일자, "A", vendor, "법인", "", 합계 - 합계 // 11, 합계 // 11, 0, 합계,
            "공제", 업태, 종목, "카과", "", "", "", "미추천", bizno]


@pytest.fixture
def srv(tmp_path):
    b = tmp_path / "kafa"
    Paths(b).ensure()
    _xlsx(b / "1_받은자료" / "고객A" / "3월.xlsx",
          [_todo("가나다인쇄소", "222-22-22227", "제조", "인쇄", "03-15", 22000),
           _todo("라마바약국", "333-33-33336", "보건", "약국", "03-20", 5500)])
    s = make_server(b, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield b, f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


def _call(url, body=None, *, guard=True, host=None):
    req = urllib.request.Request(url, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if guard:
        req.add_header("X-Kafa", "1")
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers.get("Content-Type", "") \
                else r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_page_and_empty_state(srv):
    _, url = srv
    code, page = _call(url + "/")
    assert code == 200 and "답지" in page and "X-Kafa" in page
    code, st = _call(url + "/api/state")
    assert code == 200 and st["sheet"] is None and st["rows"] == []


def test_prepare_edit_save_and_finish(srv):
    base, url = srv
    code, rep = _call(url + "/api/prepare", {})
    assert code == 200 and rep["rows"] == 2 and rep["unresolved"] == 2
    code, st = _call(url + "/api/state")
    assert code == 200 and st["has_type"] and len(st["rows"]) == 2 and st["accounts"]
    assert st["clients"] == [{"name": "고객A", "rows": 2, "left": 2, "profile": st["clients"][0]["profile"]}]
    rows = {r["vendor"]: r for r in st["rows"]}
    p, ph = rows["가나다인쇄소"], rows["라마바약국"]
    assert p["source"] == "없음" and p["type_suggested"] == "카과"

    code, res = _call(url + "/api/save", {"changes": [
        {"file": p["file"], "row": p["row"], "chosen": "도서인쇄비", "type_chosen": "일반"},
        {"file": ph["file"], "row": ph["row"], "exclude": True, "memo": "업무무관"}]})
    assert code == 200 and res["saved"] == 2
    got = {r["file"] + str(r["row"]): r for r in read_sheet(base / "3_답지" / "답지.xlsx")}
    a, b = got[p["file"] + str(p["row"])], got[ph["file"] + str(ph["row"])]
    assert a["chosen"] == "(판)도서인쇄비" and a["type_chosen"] == "일반"      # 이름 일부도 정식 이름으로
    assert b["exclude"] == "예" and b["memo"] == "업무무관"
    ws = openpyxl.load_workbook(base / "3_답지" / "답지.xlsx")["답지"]
    assert ws.data_validations.dataValidation                                    # 엑셀 목록은 그대로

    code, fin = _call(url + "/api/finish", {})
    assert code == 200 and fin["ok"] and fin["answered"] == 1 and fin["excluded"] == 1
    assert fin["outcomes"][0]["written"] == 1 and fin["uploads"] == 1
    _, st = _call(url + "/api/state")
    assert st["sheet"] is None                                                   # 답지는 보관함으로


def test_wrong_values_are_refused_and_nothing_is_written(srv):
    base, url = srv
    _call(url + "/api/prepare", {})
    _, st = _call(url + "/api/state")
    r = st["rows"][0]
    before = (base / "3_답지" / "답지.xlsx").read_bytes()
    code, res = _call(url + "/api/save", {"changes": [
        {"file": r["file"], "row": r["row"], "chosen": "촬영비"},
        {"file": r["file"], "row": r["row"], "type_chosen": "공제"}]})
    assert code == 400 and not res["ok"] and len(res["errors"]) == 2
    assert "촬영비" in res["errors"][0]["message"]
    assert (base / "3_답지" / "답지.xlsx").read_bytes() == before


def test_requests_from_other_sites_are_refused(srv):
    _, url = srv
    code, _ = _call(url + "/api/prepare", {}, guard=False)
    assert code == 403
    code, _ = _call(url + "/api/state", host="evil.example")
    assert code == 403
