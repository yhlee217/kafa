"""수임처 속성 조사표 — 엑셀로 받아 config/clients.yaml 로 바꾼다.

담당자만 아는 사실(개인/법인·직원 유무)을 자료로는 알 수 없어 사람이 적어야 한다.
YAML 을 직접 편집하게 하는 대신, **엑셀 양식**을 채워 오면 변환한다(실무자 친화).

배경·근거: docs/domain_notes.md ("직원이 없으면 복리후생비가 성립하지 않는다" 등).
"""
from __future__ import annotations

from pathlib import Path

SHEET = "수임처"
COLUMNS = ["수임처 이름", "개인/법인", "직원 있나요?", "업종(선택)", "비고(선택)"]

_YES = {"예", "있음", "y", "yes", "o", "1", "true"}
_NO = {"아니오", "아니요", "없음", "n", "no", "x", "0", "false"}
_INDIVIDUAL = {"개인", "개인사업자", "individual"}
_CORPORATE = {"법인", "법인사업자", "corporate"}

_HOWTO = [
    ["수임처 속성 조사표 — 작성 방법"],
    [],
    ["이 표는 프로그램이 전표를 더 정확히 분류하기 위해 필요합니다."],
    ["자료(엑셀)만으로는 알 수 없고, 담당자만 아는 사실이라 직접 적어 주셔야 합니다."],
    [],
    ["1. '수임처' 시트에 거래처를 한 줄에 하나씩 적어 주세요."],
    ["2. '개인/법인' 과 '직원 있나요?' 는 칸을 클릭하면 목록에서 고를 수 있습니다."],
    ["3. 업종·비고는 안 적으셔도 됩니다."],
    [],
    ["왜 '직원 있나요?' 를 묻나요"],
    ["  직원이 없으면 복리후생비가 성립하지 않아, 마트·편의점·식사 같은 소액이"],
    ["  접대비로 가야 합니다. 이걸 알면 프로그램이 계정을 훨씬 잘 맞춥니다."],
    [],
    ["왜 '개인/법인' 을 묻나요"],
    ["  법인은 카드 부채(미지급비용)를 반드시 잡아야 하고, 개인은 인출금으로 갑니다."],
    ["  처리 방식이 아예 달라서 구분이 필요합니다."],
    [],
    ["※ 사업자번호·거래처 실명 같은 민감정보는 적지 않으셔도 됩니다."],
]


def write_template(path, clients: list[str] | None = None) -> Path:
    """빈 조사표(.xlsx) 생성. clients 를 주면 이름 칸을 미리 채운다."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    head = PatternFill("solid", fgColor="E4F0EE")
    for c in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = head
        cell.alignment = Alignment(horizontal="center")
    for name in (clients or []):
        ws.append([name, "", "", "", ""])

    rows = max(len(clients or []) + 1, 200)
    dv_type = DataValidation(type="list", formula1='"개인,법인"', allow_blank=True)
    dv_emp = DataValidation(type="list", formula1='"예,아니오"', allow_blank=True)
    ws.add_data_validation(dv_type)
    ws.add_data_validation(dv_emp)
    dv_type.add(f"B2:B{rows}")
    dv_emp.add(f"C2:C{rows}")

    for col, width in zip("ABCDE", (24, 12, 14, 18, 34)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    how = wb.create_sheet("작성방법")
    for line in _HOWTO:
        how.append(line)
    how.column_dimensions["A"].width = 78
    how["A1"].font = Font(bold=True, size=13)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def _norm(v) -> str:
    return "" if v is None else str(v).strip()


# 이름 컬럼 탐지 키워드(구체적인 것 우선). 값을 추측하지 않고 **구조**로 찾는다.
NAME_HEADER_KEYS = ("거래처명", "수임처명", "사업장명", "업체명", "회사명",
                    "상호", "거래처", "수임처", "성명", "이름")
_SKIP_VALUES = {"합계", "소계", "총계", "계"}


def names_from_excel(path, *, max_header_scan: int = 20) -> list[str]:
    """거래처 목록 엑셀(위하고 '거래처 내보내기' 등)에서 이름만 뽑는다.

    헤더 위치·컬럼 순서를 모르므로 상단을 훑어 이름 컬럼을 **구조로** 찾는다.
    중복·합계행은 제외하고 등장 순서를 유지한다. 이름 외 다른 값은 읽지 않는다.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[str] = []
    seen: set[str] = set()
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            rows.append(row)
            if i > 5000:                      # 아주 큰 파일 방어
                break
        col = None
        header_i = 0
        for i, row in enumerate(rows[:max_header_scan]):
            best = (0, None)
            for j, cell in enumerate(row or []):
                text = _norm(cell).replace(" ", "")
                if not text or len(text) > 10:
                    continue          # 긴 문장은 헤더가 아니라 제목/안내문
                if text in NAME_HEADER_KEYS:
                    score = 3         # 정확히 '거래처명' 등
                elif any(k in text for k in NAME_HEADER_KEYS):
                    score = 2         # '거래처명(상호)' 같은 변형
                else:
                    continue
                if score > best[0]:
                    best = (score, j)
            if best[1] is not None:
                col, header_i = best[1], i
                break
        if col is None:
            continue
        for row in rows[header_i + 1:]:
            name = _norm(row[col] if row and len(row) > col else "")
            if not name or name in _SKIP_VALUES or name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def names_from_inbox(inbox) -> list[str]:
    """inbox 하위 폴더명 = 고객 ID(파이프라인 규칙)."""
    p = Path(inbox)
    if not p.exists():
        return []
    return sorted(d.name for d in p.iterdir()
                  if d.is_dir() and not d.name.startswith("_"))


def names_from_text(path) -> list[str]:
    """한 줄에 하나씩 적은 목록 파일."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def parse_template(path) -> dict[str, dict]:
    """채워진 조사표 → {수임처: {client_type, has_employees, note}}."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    out: dict[str, dict] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue                       # 헤더
        name = _norm(row[0] if len(row) > 0 else "")
        if not name:
            continue
        ctype = _norm(row[1] if len(row) > 1 else "").lower()
        emp = _norm(row[2] if len(row) > 2 else "").lower()
        업종 = _norm(row[3] if len(row) > 3 else "")
        비고 = _norm(row[4] if len(row) > 4 else "")

        prof: dict = {}
        if ctype in _INDIVIDUAL:
            prof["client_type"] = "individual"
        elif ctype in _CORPORATE:
            prof["client_type"] = "corporate"
        if emp in _YES:
            prof["has_employees"] = True
        elif emp in _NO:
            prof["has_employees"] = False
        note = " / ".join(x for x in (업종, 비고) if x)
        if note:
            prof["note"] = note
        out[name] = prof
    return out


def to_yaml(profiles: dict[str, dict], *, defaults: dict | None = None) -> str:
    """조사표 결과 → clients.yaml 텍스트."""
    import yaml

    doc = {
        "defaults": defaults or {"client_type": "corporate", "has_employees": True},
        "clients": profiles,
    }
    header = (
        "# config/clients.yaml — 수임처 속성 (kafa-clients import 로 생성)\n"
        "# 담당자만 아는 사실이라 조사표(엑셀)로 받아 변환한다.\n"
        "# 배경·근거: docs/domain_notes.md\n\n"
    )
    return header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
