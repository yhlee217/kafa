"""답지 화면·kafa-desk 시험용 가짜 자료 — 지어낸 수임처·거래처만(실제 세무 자료 없음).

개발용 PC 에서 화면과 처리 흐름을 끝까지 돌려 볼 때 쓴다(docs/dev_windows.md).

    python tools/make_demo.py [폴더]          # 기본: 홈 폴더의 kafa-demo
    python -m kafa.desk_cli screen --base [폴더]
"""
from __future__ import annotations

import sys
from pathlib import Path

COLUMNS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세", "합계", "국세청",
           "업태", "종목", "유형", "차변계정", "대변계정", "관리", "전표상태", "사업자등록번호"]


def _row(vendor, biz, 업태, 종목, day, amount, account="", status="미추천", tax=True):
    vat = amount // 11 if tax else 0
    return ["2026", day, "A", vendor, "일반", "", amount - vat, vat, 0, amount,
            "공제" if tax else "불공제", 업태, 종목, "카과", account, "", "", status, biz]


DEMO = {
    "가나상사": [_row("새벽정육점", "111-11-11111", "도소매", "식육", "11-02", 86000, tax=False),
                 _row("도담카페", "111-11-11112", "음식점", "커피", "11-03", 12500),
                 _row("한결인쇄", "111-11-11113", "제조", "인쇄", "11-06", 154000),
                 _row("바른문구", "111-11-11116", "도매", "문구", "11-12", 18700),
                 _row("바른문구", "111-11-11116", "도매", "문구", "11-01", 9900, "(판)소모품비", "확정가능"),
                 _row("늘봄식당", "111-11-11117", "음식점", "한식", "11-14", 48000)],
    "다온인쇄": [_row("소망철물", "222-22-22221", "소매", "철물", "11-20", 32000),
                 _row("고운꽃집", "222-22-22222", "소매", "화훼", "11-22", 50000)],
}


def make_demo(base: Path) -> int:
    """base/1_받은자료/<수임처>/11월.xlsx 를 만든다. 만든 행 수."""
    import openpyxl
    for client, rows in DEMO.items():
        path = base / "1_받은자료" / client / "11월.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(COLUMNS)
        for r in rows:
            ws.append(r)
        wb.save(path)
    return sum(len(v) for v in DEMO.values())


if __name__ == "__main__":
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "kafa-demo"
    print(f"가짜 자료 {make_demo(target)}건 → {target}")
