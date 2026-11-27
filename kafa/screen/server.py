"""답지 화면 — 아버님 PC 에서 브라우저로 여는 로컬 프로그램(파이썬 표준 http.server 만 쓴다).

저장소는 지금의 답지 엑셀(3_답지/답지.xlsx) 그대로다. 화면에서 고친 칸은 그 파일의 같은 칸에 쓰므로
엑셀로 열어 고쳐도, 화면으로 고쳐도 '업로드본 만들기'는 똑같이 동작한다(kafa/desk.py).

보안: 127.0.0.1 에만 열고, 다른 사이트가 이 화면에 요청을 보내지 못하게 Host 와 전용 머리글을
확인한다. 화면에는 거래처 이름이 나오지만 이 PC 밖으로 나가지 않는다(클로드에게 올리지 않는다).

    kafa-desk screen [--base 폴더] [--port 0] [--no-browser]
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

GUARD_HEADER = "X-Kafa"          # 화면이 보내는 머리글 — 다른 사이트의 요청은 이것을 못 붙인다
MAX_BODY = 5 * 1024 * 1024


class ScreenError(Exception):
    """화면에 그대로 보여 줄 말."""


@dataclass
class Ctx:
    base: Path
    config_dir: str | None = None
    lock: threading.Lock = None

    def __post_init__(self):
        self.lock = self.lock or threading.Lock()


# ── 답지 읽기·쓰기 ──

def _paths(ctx: Ctx):
    from kafa.desk import Paths
    return Paths(ctx.base).ensure()


def _source_of(basis: str, suggested: str) -> str:
    from kafa.recommend.host_picks import BASIS_PREFIX
    if not suggested:
        return "없음"
    if basis.startswith(BASIS_PREFIX):
        return "클로드"
    return "이력"


def sheet_state(ctx: Ctx) -> dict:
    """화면에 그릴 것 — 답지의 행, 계정 목록, 수임처별 남은 건수."""
    from kafa.config_loader import client_profile
    from kafa.desk import TYPE_CHOICES, _cells, _txt, _type_labels, find_sheet
    paths = _paths(ctx)
    sheet = find_sheet(paths)
    out = {"sheet": None, "rows": [], "accounts": [], "types": list(TYPE_CHOICES),
           "clients": [], "has_type": False, "legacy": False}
    if sheet is None:
        return out
    cells = _cells(sheet)
    out["sheet"] = sheet.name
    out["legacy"] = sheet.suffix.lower() == ".xls"
    if not cells:
        return out
    head = {_txt(h): i for i, h in enumerate(cells[0]) if _txt(h)}
    out["has_type"] = "확정 유형" in head

    def get(row, name):
        i = head.get(name)
        return _txt(row[i]) if i is not None and i < len(row) else ""

    from kafa.desk import _EXCLUDE_WORDS
    for row in cells[1:]:
        f, r = get(row, "파일"), get(row, "행")
        if not f or not r:
            continue
        try:
            ri = int(float(r))
        except ValueError:
            continue
        suggested, basis = get(row, "추천 계정"), get(row, "근거")
        out["rows"].append({
            "key": f"{f}#{ri}", "file": f, "row": ri, "client": get(row, "수임처"),
            "date": get(row, "거래일자"), "vendor": get(row, "거래처"), "total": get(row, "합계"),
            "업태": get(row, "업태"), "종목": get(row, "종목"), "품명": get(row, "품명"),
            "suggested": suggested, "basis": basis, "conf": get(row, "확신"),
            "source": _source_of(basis, suggested),
            "chosen": get(row, "확정 계정"),
            "type_suggested": get(row, "추천 유형") or get(row, "유형"),
            "type_chosen": get(row, "확정 유형"),
            "exclude": get(row, "올리지 않음").lower() in _EXCLUDE_WORDS,
            "memo": get(row, "메모")})
    labels = set(_type_labels(ctx.config_dir).values())
    out["types"] = [t for t in TYPE_CHOICES if t in labels]
    counts = Counter(r["client"] for r in out["rows"])
    left = Counter(r["client"] for r in out["rows"] if not r["chosen"] and not r["exclude"])
    for name in sorted(counts):
        prof = client_profile(name, ctx.config_dir)
        kind = "법인" if prof.get("client_type") == "corporate" else "개인"
        emp = "직원 있음" if prof.get("has_employees", True) else "직원 없음"
        out["clients"].append({"name": name, "rows": counts[name], "left": left[name],
                               "profile": f"{kind} · {emp}"})
    out["accounts"] = _accounts(ctx)
    return out


def _accounts(ctx: Ctx) -> list[str]:
    from kafa.desk import account_list
    from kafa.store.db import VoucherStore
    db_path = _paths(ctx).internal / "kafa.db"
    usage = {}
    if db_path.exists():
        with VoucherStore(db_path) as db:
            usage = db.account_usage()
    return account_list(ctx.config_dir, usage)


def save_changes(ctx: Ctx, changes: list[dict]) -> dict:
    """고친 칸을 답지 엑셀의 같은 칸에 쓴다. 하나라도 틀리면 아무것도 쓰지 않는다."""
    import openpyxl

    from kafa.clients import resolve_account_text
    from kafa.desk import EXCLUDE_MARK, SHEET, _txt, _type_labels, find_sheet
    paths = _paths(ctx)
    sheet = find_sheet(paths)
    if sheet is None:
        raise ScreenError("답지가 없습니다. 먼저 '처리하기'를 눌러 주세요.")
    if sheet.suffix.lower() == ".xls":
        raise ScreenError("답지가 옛 엑셀 형식(.xls)으로 저장되어 있습니다. '처리하기'를 한 번 누르면 "
                          "고치신 칸은 그대로 두고 새 형식으로 바뀝니다.")
    labels = set(_type_labels(ctx.config_dir).values())
    errors, clean = [], {}
    for ch in changes or []:
        key = (str(ch.get("file", "")), int(ch.get("row", -1)))
        item = {}
        if "chosen" in ch:
            text = str(ch.get("chosen") or "").strip()
            if text:
                code, got = resolve_account_text(text, ctx.config_dir)
                if code is None:
                    errors.append({"key": f"{key[0]}#{key[1]}", "field": "chosen",
                                   "message": f"'{text}': {got}"})
                    continue
                text = got
            item["확정 계정"] = text
        if "type_chosen" in ch:
            vat = str(ch.get("type_chosen") or "").strip()
            if vat and vat not in labels:
                errors.append({"key": f"{key[0]}#{key[1]}", "field": "type_chosen",
                               "message": f"'{vat}': 카과·일반·카면 중에서 고르세요"})
                continue
            item["확정 유형"] = vat
        if "exclude" in ch:
            item["올리지 않음"] = EXCLUDE_MARK if ch.get("exclude") else ""
        if "memo" in ch:
            item["메모"] = str(ch.get("memo") or "")[:500]
        clean[key] = item
    if errors:
        return {"ok": False, "errors": errors, "saved": 0}

    wb = openpyxl.load_workbook(sheet)
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    head = {_txt(c.value): c.column for c in ws[1] if _txt(c.value)}
    if "파일" not in head or "행" not in head:
        raise ScreenError("답지의 칸 이름이 바뀌었습니다. '처리하기'를 다시 눌러 주세요.")
    saved = 0
    for r in range(2, ws.max_row + 1):
        f = _txt(ws.cell(r, head["파일"]).value)
        try:
            ri = int(float(_txt(ws.cell(r, head["행"]).value) or "nan"))
        except ValueError:
            continue
        item = clean.get((f, ri))
        if not item:
            continue
        for name, value in item.items():
            if name in head:
                ws.cell(r, head[name]).value = value or None
        saved += 1
    try:
        wb.save(sheet)
    except PermissionError as e:
        raise ScreenError("답지가 엑셀에서 열려 있습니다. 엑셀을 닫고 다시 저장해 주세요.") from e
    return {"ok": True, "errors": [], "saved": saved}


def run_prepare(ctx: Ctx) -> dict:
    from kafa.desk import prepare
    rep = prepare(ctx.base, config_dir=ctx.config_dir)
    return {"ok": not rep.failures, "files": rep.files, "rows": rep.rows,
            "unresolved": rep.unresolved, "ai_filled": rep.ai_filled, "kept": rep.kept,
            "questions": rep.questions, "claude_notes": rep.claude_notes,
            "failures": [{"file": k, "why": str(v)} for k, v in rep.failures.items()]}


def run_finish(ctx: Ctx) -> dict:
    from kafa.desk import finish
    rep = finish(ctx.base, config_dir=ctx.config_dir)
    res = rep.result
    return {"ok": res.ok, "answered": rep.answered, "excluded": rep.excluded,
            "outcomes": [{"client": o.client, "period": o.period, "written": o.written,
                          "skipped": o.skipped, "integrity": str(o.integrity)}
                         for o in res.outcomes],
            "failures": [{"file": k, "why": str(v)} for k, v in res.failures.items()],
            "uploads": sum(1 for p in rep.copied if p.suffix.lower() == ".xls"),
            "archived": bool(rep.archived)}


# ── HTTP ──

def _page() -> bytes:
    return resources.files("kafa.screen").joinpath("dapji.html").read_bytes()


def make_handler(ctx: Ctx):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kafa-screen"

        def log_message(self, fmt, *args):      # 콘솔에 요청마다 찍지 않는다(거래처 이름이 없어도 시끄럽다)
            return

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict):
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "이 PC 에서만 열 수 있습니다."})
            if self.path in ("/", "/index.html"):
                return self._send(200, _page(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                try:
                    return self._json(200, sheet_state(ctx))
                except Exception as e:  # noqa: BLE001 — 화면에 그대로 보여 준다
                    return self._json(500, {"error": _say(e)})
            return self._json(404, {"error": "없는 주소입니다."})

        def do_POST(self):
            if not self._host_ok() or self.headers.get(GUARD_HEADER) != "1":
                return self._json(403, {"error": "이 화면에서만 보낼 수 있습니다."})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > MAX_BODY:
                return self._json(413, {"error": "보낸 내용이 너무 큽니다."})
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except json.JSONDecodeError:
                return self._json(400, {"error": "보낸 내용을 읽지 못했습니다."})
            route = {"/api/save": lambda: save_changes(ctx, body.get("changes") or []),
                     "/api/prepare": lambda: run_prepare(ctx),
                     "/api/finish": lambda: run_finish(ctx)}.get(self.path)
            if route is None:
                return self._json(404, {"error": "없는 주소입니다."})
            if not ctx.lock.acquire(blocking=False):
                return self._json(409, {"error": "다른 작업이 진행 중입니다. 끝난 뒤 다시 눌러 주세요."})
            try:
                data = route()
                # 저장할 수 없는 값이 있으면 400(무엇이 틀렸는지 errors 에), 그 밖은 결과를 그대로
                return self._json(400 if data.get("errors") else 200, data)
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": _say(e)})
            finally:
                ctx.lock.release()

    return Handler


def _say(e: Exception) -> str:
    from kafa.desk import DeskError
    if isinstance(e, (ScreenError, DeskError)):
        return str(e)
    return f"처리 중 문제가 생겼습니다({type(e).__name__}). 화면을 사진으로 찍어 아드님께 보내 주세요."


def make_server(base, *, port: int = 0, config_dir: str | None = None) -> ThreadingHTTPServer:
    from kafa.desk import base_dir
    ctx = Ctx(base=base_dir(base), config_dir=config_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctx))
    srv.daemon_threads = True
    return srv


def serve(base=None, *, port: int = 0, open_browser: bool = True,
          config_dir: str | None = None) -> None:
    srv = make_server(base, port=port, config_dir=config_dir)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"답지 화면: {url}")
    print("이 창을 닫으면 화면도 꺼집니다.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
