"""수임처 속성 조사표 — 엑셀로 받아 수임처 속성 파일(~/.kafa/clients.yaml)로 바꾼다.

담당자만 아는 사실(개인/법인·직원 유무)을 자료로는 알 수 없어 사람이 적어야 한다.
YAML 을 직접 편집하게 하는 대신, **엑셀 양식**을 채워 오면 변환한다(실무자 친화).

배경·근거: docs/domain_notes.md ("직원이 없으면 복리후생비가 성립하지 않는다" 등).
"""
from __future__ import annotations

import re
from pathlib import Path

SHEET = "수임처"
COLUMNS = ["수임처 이름", "개인/법인", "직원 있나요?", "업종(선택)", "비고(선택)"]

# 담당자가 실제로 쓰는 표기까지 받는다 — 2026-11-26 조사표는 직원 칸을 '유/무', 법인을 '법'으로
# 적어 왔다. 모르는 표기는 버리지 않고 parse_template(issues=…) 로 알린다.
_YES = {"예", "있음", "있", "유", "y", "yes", "o", "○", "1", "true"}
_NO = {"아니오", "아니요", "없음", "없", "무", "n", "no", "x", "×", "0", "false"}
_INDIVIDUAL = {"개인", "개", "개인사업자", "individual"}
_CORPORATE = {"법인", "법", "법인사업자", "corporate"}

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
    ["'직원 있나요?' 를 고르는 기준 (2026-11-23 말씀하신 대로)"],
    ["  · 아르바이트나 일용직만 있어도 '있음' 으로 골라 주세요."],
    ["  · 가족만 일하는 곳은 수임처마다 판단해서 골라 주시고, 비고에 '가족' 이라고 적어 주세요."],
    ["  · 위하고에서 확인이 안 되는 곳은 비워 두셔도 됩니다."],
    [],
    ["왜 '개인/법인' 을 묻나요"],
    ["  법인은 카드 부채(미지급비용)를 반드시 잡아야 하고, 개인은 인출금으로 갑니다."],
    ["  처리 방식이 아예 달라서 구분이 필요합니다."],
    [],
    ["※ 사업자번호·거래처 실명 같은 민감정보는 적지 않으셔도 됩니다."],
]


def write_template(path, clients=None) -> Path:
    """조사표(.xlsx) 생성.

    clients: 이름 문자열 목록, 또는 {"name":…, "client_type": "개인"|"법인"} 목록.
    알고 있는 값은 미리 채워 담당자가 고를 칸을 최소화한다.
    """
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
    for c in (clients or []):
        if isinstance(c, dict):
            ws.append([c.get("name", ""), c.get("client_type", ""), "", "", ""])
        else:
            ws.append([c, "", "", "", ""])

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
TYPE_HEADER_KEYS = ("구분", "개인법인", "법인구분", "사업자구분", "유형")
URL_HEADER_KEYS = ("접속URL", "URL", "링크", "주소", "접속주소")


def _scan_rows(ws, limit: int = 5000) -> list:
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        rows.append(row)
        if i > limit:
            break
    return rows


def _header_in(rows, max_header_scan: int):
    """(점수, 이름컬럼, 구분컬럼, 헤더행) — 못 찾으면 점수 0.

    긴 문장(제목·안내문)은 헤더로 보지 않고, '회사명' 같은 정확 일치를 부분 일치보다
    우선한다. 이렇게 해야 '총수임처' 같은 요약 문구에 낚이지 않는다.
    """
    for i, row in enumerate(rows[:max_header_scan]):
        best_score, best_col, tcol, ucol = 0, None, None, None
        for j, cell in enumerate(row or []):
            text = _norm(cell).replace(" ", "")
            if not text or len(text) > 10:
                continue
            if text in NAME_HEADER_KEYS:
                score = 3
            elif any(k in text for k in NAME_HEADER_KEYS):
                score = 2
            else:
                if text in TYPE_HEADER_KEYS and tcol is None:
                    tcol = j
                elif text.upper() in URL_HEADER_KEYS and ucol is None:
                    ucol = j
                continue
            if score > best_score:
                best_score, best_col = score, j
        if best_col is not None:
            return best_score, best_col, tcol, ucol, i
    return 0, -1, None, None, 0


def _best_table(path, *, max_header_scan: int = 20):
    """여러 시트 중 **가장 그럴듯한 표 하나**를 고른다.

    정확 일치 헤더(점수 3)를 우선하고, 같은 점수면 데이터 행이 많은 시트를 쓴다.
    요약·점검 같은 보조 시트를 함께 긁어 이름이 뒤섞이는 것을 막는다.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    best = None
    for ws in wb.worksheets:
        rows = _scan_rows(ws)
        score, ncol, tcol, ucol, hi = _header_in(rows, max_header_scan)
        if not score:
            continue
        n = sum(1 for r in rows[hi + 1:]
                if r and len(r) > ncol and _norm(r[ncol])
                and _norm(r[ncol]) not in _SKIP_VALUES)
        key = (score, n)
        if best is None or key > best[0]:
            best = (key, rows, ncol, tcol, ucol, hi)
    return best


def profiles_from_excel(path, *, max_header_scan: int = 20) -> list[dict]:
    """수임처 목록 엑셀 → [{"name":…, "client_type":…}].

    이름 컬럼은 헤더를 보고 **구조로** 찾고, 같은 표에 '구분' 컬럼이 있으면 개인/법인도
    함께 읽는다(담당자가 고를 칸이 줄어든다). 중복·합계행은 제외하고 순서를 유지한다.
    """
    best = _best_table(path, max_header_scan=max_header_scan)
    if best is None:
        return []
    _key, rows, ncol, tcol, ucol, hi = best
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows[hi + 1:]:
        name = _norm(row[ncol] if row and len(row) > ncol else "")
        if not name or name in _SKIP_VALUES or name in seen:
            continue
        seen.add(name)
        rec = {"name": name}
        if tcol is not None and row and len(row) > tcol:
            t = _norm(row[tcol])
            if t in _INDIVIDUAL:
                rec["client_type"] = "개인"
            elif t in _CORPORATE:
                rec["client_type"] = "법인"
        if ucol is not None and row and len(row) > ucol:
            url = _norm(row[ucol])
            if url.startswith("http"):
                rec["url"] = url
        out.append(rec)
    return out


def client_urls_from_csv(path) -> dict[str, str]:
    """kafa-fetch --discover 가 만든 목록(CSV) → {회사명: 접속 URL}."""
    import csv as _csv
    out: dict[str, str] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        for row in _csv.DictReader(fh):
            name = _norm(row.get("회사명"))
            url = _norm(row.get("접속 URL"))
            if name and url.startswith("http"):
                out[name] = url
    return out


def client_cnos_from_csv(path) -> dict[str, str]:
    """kafa-fetch --discover 목록(CSV) → {회사명: 수임처코드}."""
    import csv as _csv
    out: dict[str, str] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        for row in _csv.DictReader(fh):
            name, cno = _norm(row.get("회사명")), _norm(row.get("수임처코드"))
            if name and cno:
                out[name] = cno
    return out


_CNO_IN_URL = re.compile(r"[?&]cno=(\d+)")


def client_cnos_from_excel(path) -> dict[str, str]:
    """수임처 마스터 엑셀 → {회사명: 수임처코드}.

    접속 URL 안에 `cno=<코드>` 가 들어 있다. 주소 전체는 수임처마다 다른 값
    (cd_com·gisu·companyID·taxNum)이 섞여 있어 쓸 수 없지만, **코드만은** 그대로
    쓸 수 있다 — 목록에서 `a#tooltip_<코드>` 를 눌러 여는 데 필요한 값이다.
    """
    out: dict[str, str] = {}
    for name, url in client_urls_from_excel(path).items():
        m = _CNO_IN_URL.search(url)
        if m:
            out[name] = m.group(1)
    return out


def client_cnos(path) -> dict[str, str]:
    """수임처 목록 파일 → {회사명: 수임처코드}. CSV 는 코드 칸, 엑셀은 URL 에서 추출."""
    return (client_cnos_from_csv(path) if str(path).lower().endswith(".csv")
            else client_cnos_from_excel(path))


def client_urls(path) -> dict[str, str]:
    """수임처 목록 파일 → {회사명: 접속 URL}. .csv 와 엑셀 둘 다 받는다."""
    return (client_urls_from_csv(path) if str(path).lower().endswith(".csv")
            else client_urls_from_excel(path))


def client_urls_from_excel(path) -> dict[str, str]:
    """수임처 마스터 → {회사명: 접속 URL}. URL 컬럼이 없으면 빈 dict.

    로그인은 사람이 하고, **이미 로그인된 세션**에서 이 URL로 바로 이동해 화면 탐색
    단계를 줄인다(거래처 검색·선택 클릭 불필요).
    """
    return {r["name"]: r["url"] for r in profiles_from_excel(path) if r.get("url")}


def names_from_excel(path, *, max_header_scan: int = 20) -> list[str]:
    """거래처/수임처 목록 엑셀에서 이름만 뽑는다."""
    return [r["name"] for r in
            profiles_from_excel(path, max_header_scan=max_header_scan)]


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


def _template_rows(path) -> list[tuple]:
    """조사표의 '수임처' 시트 행들. 엑셀에서 다시 저장한 옛 형식(.xls)도 읽는다."""
    path = Path(path)
    if path.suffix.lower() == ".xls":
        import xlrd

        wb = xlrd.open_workbook(str(path))
        names = wb.sheet_names()
        sh = wb.sheet_by_name(SHEET) if SHEET in names else wb.sheet_by_index(0)
        return [tuple(sh.cell_value(r, c) for c in range(sh.ncols))
                for r in range(sh.nrows)]
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    return list(ws.iter_rows(values_only=True))


def parse_template(path, issues: list | None = None) -> dict[str, dict]:
    """채워진 조사표 → {client_id: {client_type, has_employees, note}}.

    client_id 는 파이프라인의 고객 폴더명과 같아야 하므로 safe_name 을 쓴다(마스터와 같은 규칙).
    issues 를 주면 알아듣지 못한 표기를 (행 번호, 칸, 값) 으로 담는다 — 조용히 버리지 않는다.
    """
    from kafa.fetch.plan import safe_name

    out: dict[str, dict] = {}
    for i, row in enumerate(_template_rows(path)):
        if i == 0:
            continue                       # 헤더
        name = _norm(row[0] if len(row) > 0 else "")
        if not name:
            continue
        ctype_raw = _norm(row[1] if len(row) > 1 else "")
        emp_raw = _norm(row[2] if len(row) > 2 else "")
        ctype, emp = ctype_raw.lower(), emp_raw.lower()
        업종 = _norm(row[3] if len(row) > 3 else "")
        비고 = _norm(row[4] if len(row) > 4 else "")
        # 비고 오른쪽 빈 칸에 적은 메모(예: '법인전환')도 버리지 않는다
        더 = [_norm(v) for v in row[5:] if _norm(v)]

        prof: dict = {}
        if ctype in _INDIVIDUAL:
            prof["client_type"] = "individual"
        elif ctype in _CORPORATE:
            prof["client_type"] = "corporate"
        elif ctype and issues is not None:
            issues.append((i + 1, "개인/법인", ctype_raw))
        if emp in _YES:
            prof["has_employees"] = True
        elif emp in _NO:
            prof["has_employees"] = False
        elif emp and issues is not None:
            issues.append((i + 1, "직원 있나요?", emp_raw))
        note = " / ".join(x for x in (업종, 비고, *더) if x)
        if note:
            prof["note"] = note
        out[safe_name(name)] = prof
    return out


def profiles_from_master(path) -> dict[str, dict]:
    """수임처 마스터 엑셀 → {client_id: {client_type, name}}.

    마스터의 '구분'(개인/법인)만 옮긴다. **직원 유무는 넣지 않는다** — 자료로는 알 수
    없는 사실이라 담당자가 조사표로 답해야 한다(docs/domain_notes.md).
    client_id 는 파이프라인의 고객 폴더명과 같아야 하므로 safe_name 을 쓴다.
    """
    from kafa.fetch.plan import safe_name

    out: dict[str, dict] = {}
    for r in profiles_from_excel(path):
        name = r.get("name") or ""
        if not name:
            continue
        prof: dict = {"name": name}
        t = (r.get("client_type") or "").strip()
        if t in _INDIVIDUAL:
            prof["client_type"] = "individual"
        elif t in _CORPORATE:
            prof["client_type"] = "corporate"
        out[safe_name(name)] = prof
    return out


def load_yaml_profiles(path) -> tuple[dict, dict]:
    """기존 clients.yaml → (defaults, clients). 없으면 빈 값."""
    import yaml

    p = Path(path)
    if not p.exists():
        return {}, {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return (data.get("defaults") or {}), (data.get("clients") or {})


def merge_profiles(existing: dict, new: dict) -> dict:
    """기존에 사람이 적어 둔 값(직원 유무·비고)을 지우지 않고 합친다.

    마스터에서 온 값(구분·이름)은 덮어쓰고, 사람이 답한 항목은 그대로 둔다.
    """
    out = {k: dict(v or {}) for k, v in (existing or {}).items()}
    for cid, prof in (new or {}).items():
        cur = out.setdefault(cid, {})
        cur.update({k: v for k, v in prof.items() if v is not None})
    return out


def to_yaml(profiles: dict[str, dict], *, defaults: dict | None = None) -> str:
    """조사표 결과 → clients.yaml 텍스트(수임처 실명이 들어간다 — 레포 밖에 둔다)."""
    import yaml

    doc = {
        "defaults": defaults or {"client_type": "corporate", "has_employees": True},
        "clients": profiles,
    }
    header = (
        "# 수임처 속성 (kafa-clients 로 생성) — 수임처 실명이 들어 있다. git 에 넣지 않는다.\n"
        "# 담당자만 아는 사실이라 조사표(엑셀)로 받아 변환한다.\n"
        "# 배경·근거: docs/domain_notes.md\n\n"
    )
    return header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def resolve_account_text(text, config_dir: str | None = None) -> tuple[int | None, str]:
    """'접대비'·'813'·'(판)접대비(기업업무추진비)' → (코드, 계정 이름) 또는 (None, 이유).

    이름 일부만 주면 판매관리비 (판) 쪽을 고른다(해외접대비 같은 갈래는 뺀다). 대변 쪽 계정은
    받지 않는다. 계정 코드표에 없는 계정(수임처가 따로 만든 것)은 아직 받지 않는다.
    """
    from kafa.config_loader import load_account_codes, load_credit_side_codes

    credit = load_credit_side_codes(config_dir)
    every = load_account_codes(config_dir)
    mapping = {n: c for n, c in every.items() if c not in credit}
    by_code: dict[int, str] = {}
    for n, c in mapping.items():
        by_code.setdefault(c, n)
    t = str(text or "").strip()
    if not t:
        return None, "계정을 적어 주세요(예: 접대비, 813)"
    if t.isdigit():
        code = int(t)
        if code in by_code:
            return code, by_code[code]
        if code in credit:
            return None, f"{code} 는 대변 쪽 계정이라 식대 계정으로 쓸 수 없습니다"
        return None, f"계정 코드표에 없는 코드입니다: {code}"

    def norm(x: str) -> str:
        return re.sub(r"\s+", "", x)

    for n, c in mapping.items():
        if norm(n) == norm(t):
            return c, n
    cands = [(n, c) for n, c in mapping.items() if norm(t) in norm(n) and "해외" not in n]
    pan = [x for x in cands if x[0].startswith("(판)")]
    pick = pan if len(pan) == 1 else (cands if len(cands) == 1 else [])
    if pick:
        return pick[0][1], pick[0][0]
    if not cands:
        return None, f"계정 코드표에 '{t}' 이(가) 없습니다(수임처가 따로 만든 계정이면 아직 지정할 수 없습니다)"
    return None, ("여러 계정이 맞습니다: " + ", ".join(n for n, _ in cands[:6])
                  + " — 정확한 이름이나 코드로 주세요")
