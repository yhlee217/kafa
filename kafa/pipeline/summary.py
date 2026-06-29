"""고객 진행 현황 보드 — SQLite 누적 데이터를 한 화면 요약(텍스트 + HTML).

고객별 누적 건수·기간·최근 처리·'확인 필요'(검토+미해소)를 모아 담당자가 한눈에 본다.
값은 비-PII 수치/기간뿐(거래처 실명·금액 없음). client_id 는 사용자가 정한 폴더명(로컬).
"""
from __future__ import annotations

from pathlib import Path

from kafa.store.db import VoucherStore


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_board(db: VoucherStore) -> list[dict]:
    rows = db.board_rows()
    for r in rows:
        r["확인필요"] = (r["review"] or 0) + (r["unresolved"] or 0)
    return rows


def render_board_text(rows: list[dict]) -> str:
    if not rows:
        return "── 고객 진행 현황 ──\n(아직 처리된 데이터 없음)"
    lines = ["── 고객 진행 현황 ──"]
    tv = tn = 0
    for r in rows:
        flag = " ⚠️" if r["확인필요"] else ""
        lines.append(f"  {r['client_id']}: 누적 {r['vouchers']}건 / "
                     f"기간 {r['periods']} / 최근 {r['latest_period'] or '-'} / "
                     f"확인필요 {r['확인필요']}{flag}")
        tv += r["vouchers"]; tn += r["확인필요"]
    lines.append(f"\n고객 {len(rows)}곳 · 누적 {tv}건 · 확인 필요 {tn}건")
    return "\n".join(lines)


def render_board_html(rows: list[dict]) -> str:
    head = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<title>kafa 고객 진행 현황</title><style>"
        "body{font-family:'Malgun Gothic',sans-serif;margin:24px;color:#222}"
        "h1{font-size:20px}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:6px 10px;text-align:right}"
        "th{background:#f3f3f3}td.name{text-align:left}"
        "tr.warn td{background:#fff3e0}.ok{color:#2e7d32}"
        ".warn-n{color:#e65100;font-weight:bold}.foot{margin-top:12px;color:#555}"
        "</style></head><body><h1>kafa 고객 진행 현황</h1>"
    )
    if not rows:
        return head + "<p>아직 처리된 데이터가 없습니다.</p></body></html>"
    th = ("<tr><th class='name'>고객</th><th>건수</th><th>기간수</th>"
          "<th>최근기간</th><th>확인필요</th><th>최근처리</th></tr>")
    body = []
    tv = tn = 0
    for r in rows:
        warn = r["확인필요"] > 0
        body.append(
            f"<tr class='{'warn' if warn else ''}'>"
            f"<td class='name'>{_esc(r['client_id'])}</td>"
            f"<td>{r['vouchers']}</td><td>{r['periods']}</td>"
            f"<td>{_esc(r['latest_period'])}</td>"
            f"<td class='{'warn-n' if warn else 'ok'}'>{r['확인필요']}</td>"
            f"<td>{_esc((r['last_ingested'] or '')[:16])}</td></tr>")
        tv += r["vouchers"]; tn += r["확인필요"]
    foot = f"<p class='foot'>고객 {len(rows)}곳 · 누적 {tv}건 · 확인 필요 {tn}건</p>"
    return head + "<table>" + th + "".join(body) + "</table>" + foot + "</body></html>"


def write_board(out_dir) -> Path | None:
    """out_dir/kafa.db 를 읽어 _board.txt · _board.html 생성. DB 없으면 None."""
    out_dir = Path(out_dir)
    db_path = out_dir / "kafa.db"
    if not db_path.exists():
        return None
    with VoucherStore(db_path) as db:
        rows = build_board(db)
    (out_dir / "_board.txt").write_text(render_board_text(rows), encoding="utf-8")
    html = out_dir / "_board.html"
    html.write_text(render_board_html(rows), encoding="utf-8")
    return html
