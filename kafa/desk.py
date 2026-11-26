"""아버님 PC(Windows)용 — 폴더에 파일을 넣고 더블클릭 두 번.

    처리하기          받은 자료 → 답지(엑셀) + 클로드에게 보낼 파일. 클로드 답 파일이 있으면 먼저 반영한다.
    업로드본_만들기   답지의 '확정 계정'을 넣어 업로드본(.xls)을 만들고, 그 답을 처리 이력에 쌓는다.

폴더(기본 ~/kafa — Windows 면 C:\\Users\\<이름>\\kafa):

    1_받은자료/<수임처>/       위하고 '신용카드' 다운로드본(.xlsx)을 수임처 폴더에 넣는다
    2_클로드/                  클로드에게_보낼_질문.json · 클로드에게_붙여넣을_말.txt
                               → 클로드가 준 답(.json 또는 .txt)을 여기에 넣는다
    3_답지/답지.xlsx           아버님이 '확정 계정' 칸을 확인·수정한다
    4_업로드본/<수임처>/<달>/  위하고에 올릴 .xls 와 검토 리포트
    _내부/                     처리 이력(kafa.db)·답 모음(_ai)·처리 끝난 원본(_archive) — 손대지 않는다

답지의 답은 위하고가 채운 계정과 똑같이 다룬다(처리된 행) — 그래서 다음 달부터 같은 가맹점에
자동으로 쓰인다(수임처 이력). '올리지 않음'은 업로드본에서 빼고 스킵으로 집계한다(대사 그대로).

보안: 답지·업로드본은 아버님 PC 안에서만 쓴다(실명). 클로드에게 가는 파일에는 업태·종목·품명·
유형과 건수만 있다(kafa/recommend/host_picks.py). 쓰는 법: docs/desk.md.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

INBOX = "1_받은자료"
CLAUDE = "2_클로드"
SHEETS = "3_답지"
UPLOADS = "4_업로드본"
INTERNAL = "_내부"
DONE = "_반영됨"

SHEET_FILE = "답지.xlsx"
QUESTION_FILE = "클로드에게_보낼_질문.json"
PROMPT_FILE = "클로드에게_붙여넣을_말.txt"

SHEET = "답지"
ACCOUNT_SHEET = "계정목록"
HOWTO_SHEET = "작성방법"
COLUMNS = ["수임처", "거래일자", "거래처", "합계", "업태", "종목", "품명", "유형",
           "추천 계정", "근거", "확신", "확정 계정", "올리지 않음", "메모", "파일", "행"]
EXCLUDE_MARK = "예"
_EXCLUDE_WORDS = {"예", "y", "yes", "o", "○", "v", "✓", "1", "true", "제외", "뺌"}
EXCLUDED_STATUS = "담당자제외"      # config skip_status 에 같은 말이 있어야 한다

_HOWTO = [
    "답지 작성 방법",
    "",
    "1. '확정 계정' 칸에 들어 있는 계정이 그대로 업로드본에 들어갑니다.",
    "   프로그램이나 클로드가 추천한 계정이 미리 채워져 있습니다. 틀리면 목록에서 고르세요.",
    "2. 비어 있는 칸은 추천이 없는 건입니다. 계정을 고르거나, 비워 두면 계정 없이(미해소) 올라갑니다.",
    "3. 업로드하지 않을 건(업무무관 등)은 '올리지 않음' 칸에 '예'를 고르세요.",
    "4. 다 했으면 저장하고 엑셀을 닫은 뒤 '업로드본_만들기'를 누르세요.",
    "",
    "여기서 고른 계정은 다음 달부터 같은 가맹점에 자동으로 쓰입니다.",
    "정렬·필터는 마음대로 하셔도 됩니다(숨긴 '파일'·'행' 칸으로 원래 행을 찾습니다).",
    "이 파일에는 거래처 이름이 있으니 클로드에게 올리지 마세요. 클로드에게는 2_클로드 폴더의 파일만 올립니다.",
]


class DeskError(Exception):
    """아버님께 그대로 보여 줄 말(무엇이 잘못됐고 어떻게 하면 되는지)."""


@dataclass
class Paths:
    base: Path

    @property
    def inbox(self) -> Path:
        return self.base / INBOX

    @property
    def claude(self) -> Path:
        return self.base / CLAUDE

    @property
    def sheets(self) -> Path:
        return self.base / SHEETS

    @property
    def uploads(self) -> Path:
        return self.base / UPLOADS

    @property
    def internal(self) -> Path:
        return self.base / INTERNAL

    @property
    def sheet(self) -> Path:
        return self.sheets / SHEET_FILE

    def ensure(self) -> Paths:
        for d in (self.inbox, self.claude, self.sheets, self.uploads, self.internal):
            d.mkdir(parents=True, exist_ok=True)
        return self


def base_dir(arg=None) -> Path:
    return Path(arg).expanduser() if arg else Path.home() / "kafa"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ── 클로드 답 받기 ──

def extract_answers(text: str):
    """클로드가 준 답(파일 그대로, 또는 대화창에서 복사해 메모장에 붙인 글) → {"answers": [...]} 또는 None."""
    def _ok(obj):
        if isinstance(obj, dict) and isinstance(obj.get("answers"), list):
            return obj
        if isinstance(obj, list) and all(isinstance(a, dict) for a in obj):
            return {"answers": obj}
        return None

    text = (text or "").strip()
    try:
        got = _ok(json.loads(text))
        if got:
            return got
    except ValueError:
        pass
    for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL):
        try:
            got = _ok(json.loads(block))
            if got:
                return got
        except ValueError:
            continue
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return _ok(json.loads(text[start:end + 1]))
        except ValueError:
            return None
    return None


def ingest_claude_answers(paths: Paths, *, config_dir: str | None = None) -> list[str]:
    """2_클로드 에 넣은 답 파일을 답 모음에 합친다. 반영한 파일은 _반영됨 으로 옮긴다."""
    from kafa.recommend.host_picks import ANSWERS, ai_dir, merge_answers

    notes: list[str] = []
    for f in sorted(p for p in paths.claude.iterdir() if p.is_file()):
        if f.name in (QUESTION_FILE, PROMPT_FILE) or f.suffix.lower() not in (".json", ".txt"):
            continue
        data = extract_answers(f.read_text(encoding="utf-8-sig", errors="replace"))
        if data is None:
            notes.append(f"{f.name}: 답을 찾지 못했습니다(클로드가 준 JSON 이 들어 있는지 확인)")
            continue
        d = ai_dir(paths.internal)
        d.mkdir(parents=True, exist_ok=True)
        (d / ANSWERS).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        res = merge_answers(paths.internal, config_dir=config_dir)
        line = res.line() if res is not None else "반영할 답 없음"
        if res is not None and res.rejected:
            line += " — 받지 않은 것은 질문지에 없던 번호이거나 허용되지 않은 계정입니다"
        notes.append(f"{f.name}: {line}")
        done = paths.claude / DONE
        done.mkdir(exist_ok=True)
        shutil.move(str(f), str(done / f"{_stamp()}_{f.name}"))
    return notes


def write_prompt(paths: Paths) -> Path:
    """클로드에게 함께 붙여넣을 말. 판단 기준은 docs/cowork_ask.md 의 것을 그대로 가져온다."""
    rules = ""
    doc = Path(__file__).resolve().parent.parent / "docs" / "cowork_ask.md"
    if doc.exists():
        m = re.search(r"## 판단 기준[^\n]*\n(.*?)(?:\n## |\Z)", doc.read_text(encoding="utf-8"), re.DOTALL)
        if m:
            rules = "판단 기준(담당자 확인):\n" + m.group(1).strip() + "\n\n"
    text = (
        "첨부한 '클로드에게_보낼_질문.json' 은 카드 매입 전표 중 차변계정(어떤 비용인지)을 "
        "정하지 못한 건들의 업종 정보입니다. 거래처·사업자번호·금액은 일부러 뺐습니다.\n\n"
        "각 질문의 업태·종목·품명·유형만 보고 accounts 에서 계정 하나를 골라 주세요. "
        "판단이 서지 않으면 code 를 null(모름)로 두세요. 추측으로 채우지 마세요.\n\n"
        + rules +
        "답은 아래 JSON 하나로만 주세요(설명 없이). 파일로 줄 수 있으면 '클로드_답.json' 으로 주세요.\n"
        '{"answers": [{"id": "질문의 id 그대로", "code": 830, "confidence": 0.8, '
        '"why": "한 줄 근거"}]}\n'
        "confidence 는 0~1 로 솔직하게. 0.5 미만은 적용되지 않습니다.\n"
    )
    p = paths.claude / PROMPT_FILE
    p.write_text(text, encoding="utf-8")
    return p


# ── 답지 ──

@dataclass
class SheetRow:
    client: str
    date: str
    vendor: str
    total: str
    업태: str
    종목: str
    품명: str
    유형: str
    suggested: str
    basis: str
    conf: str
    chosen: str
    exclude: str
    memo: str
    file: str
    row: int

    @property
    def key(self) -> tuple[str, int]:
        return (self.file, self.row)


def _names(config_dir) -> dict[int, str]:
    from kafa.recommend.host_picks import account_names
    return account_names(config_dir)


def sheet_row(client: str, file_rel: str, c, names: dict[int, str]) -> SheetRow | None:
    """아버님이 볼 행인가 — 계정을 못 정했거나(미해소), 프로그램이 추천했거나, 확인 표시가 있으면."""
    from kafa.rules.models import Verdict
    if c.skipped or c.source is None or c.source.raw_index is None:
        return None
    if not (c.판정유형 in (Verdict.UNRESOLVED, Verdict.RECOMMENDED) or c.needs_review):
        return None
    r = c.source
    suggested = names.get(c.차변계정코드, str(c.차변계정코드)) if c.차변계정코드 else ""
    parts = [c.추천근거] if c.추천근거 else []
    if c.review_reasons:
        parts.append("확인: " + "; ".join(c.review_reasons))
    if not parts and c.판정유형 == Verdict.UNRESOLVED:
        parts.append("추천 없음")
    conf = f"{c.신뢰도:.0%}" if c.신뢰도 is not None and c.판정유형 == Verdict.RECOMMENDED else ""
    return SheetRow(
        client=client, date=f"{r.연도}-{r.일자}".strip("-"), vendor=r.거래처 or "",
        total=str(r.합계), 업태=r.업태 or "", 종목=r.종목 or "", 품명=r.품명 or "",
        유형=r.유형 or "", suggested=suggested, basis=" / ".join(parts), conf=conf,
        chosen=suggested, exclude="", memo="", file=file_rel, row=int(r.raw_index))


def find_sheet(paths: Paths) -> Path | None:
    """답지 — 엑셀에서 옛 형식(.xls)으로 저장했을 수도 있다. 더 최근 것을 쓴다."""
    cands = [p for p in (paths.sheet, paths.sheet.with_suffix(".xls")) if p.exists()]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _cells(path: Path) -> list[list]:
    if path.suffix.lower() == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        sh = wb.sheet_by_name(SHEET) if SHEET in wb.sheet_names() else wb.sheet_by_index(0)
        return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def read_sheet(path: Path) -> list[dict]:
    """답지 → 행 목록. 칸은 머리글 이름으로 찾는다(아버님이 칸을 옮겨도 된다)."""
    rows = _cells(path)
    if not rows:
        return []
    head = {_txt(h): i for i, h in enumerate(rows[0]) if _txt(h)}
    missing = [h for h in ("확정 계정", "올리지 않음", "파일", "행") if h not in head]
    if missing:
        raise DeskError(f"답지의 칸 이름이 바뀌었습니다({', '.join(missing)}). "
                        "칸 이름은 그대로 두고 다시 저장해 주세요.")

    def get(row, name):
        i = head.get(name)
        return _txt(row[i]) if i is not None and i < len(row) else ""

    out = []
    for n, row in enumerate(rows[1:], start=2):
        f, r = get(row, "파일"), get(row, "행")
        if not f or not r:
            continue
        try:
            ri = int(float(r))
        except ValueError:
            continue
        out.append({"sheet_row": n, "file": f, "row": ri, "client": get(row, "수임처"),
                    "total": get(row, "합계"), "suggested": get(row, "추천 계정"),
                    "chosen": get(row, "확정 계정"), "exclude": get(row, "올리지 않음"),
                    "memo": get(row, "메모")})
    return out


def account_list(config_dir=None, usage: dict | None = None) -> list[str]:
    """답지 목록에 넣을 계정 — 이 사무실이 많이 쓴 순서, 그다음 나머지(코드 순)."""
    names = _names(config_dir)
    used = [c for c, _ in sorted((usage or {}).items(), key=lambda kv: (-kv[1], kv[0]))
            if c in names]
    rest = [c for c in sorted(names) if c not in set(used)]
    return [names[c] for c in used + rest]


def _keep_father_edits(new: list[SheetRow], existing: list[dict]) -> int:
    """다시 만들 때 아버님이 이미 고친 칸은 살린다. 고치지 않은 추천은 새 추천으로 바꾼다."""
    old = {(e["file"], e["row"]): e for e in existing}
    kept = 0
    for r in new:
        e = old.get(r.key)
        if not e:
            continue
        changed = False
        if e["chosen"] != e["suggested"]:          # 아버님이 바꿨거나 지웠다
            r.chosen = e["chosen"]
            changed = True
        if e["exclude"] or e["memo"]:
            r.exclude, r.memo = e["exclude"], e["memo"]
            changed = True
        kept += changed
    return kept


def write_sheet(path: Path, rows: list[SheetRow], accounts: list[str]) -> Path:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(COLUMNS)
    head = PatternFill("solid", fgColor="E4F0EE")
    mine = PatternFill("solid", fgColor="FBEFDC")
    for c in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = mine if COLUMNS[c - 1] in ("확정 계정", "올리지 않음", "메모") else head
        cell.alignment = Alignment(horizontal="center")
    for r in rows:
        try:
            total = int(Decimal(r.total)) if Decimal(r.total) == Decimal(r.total).to_integral() \
                else float(r.total)
        except (InvalidOperation, ValueError):
            total = r.total
        ws.append([r.client, r.date, r.vendor, total, r.업태, r.종목, r.품명, r.유형,
                   r.suggested, r.basis, r.conf, r.chosen, r.exclude, r.memo, r.file, r.row])
    last = max(len(rows) + 1, 2)
    col = {name: openpyxl.utils.get_column_letter(i + 1) for i, name in enumerate(COLUMNS)}
    for row in ws.iter_rows(min_row=2, max_row=last):
        row[COLUMNS.index("합계")].number_format = "#,##0"
        for name in ("확정 계정", "올리지 않음", "메모"):
            row[COLUMNS.index(name)].fill = mine

    lists = wb.create_sheet(ACCOUNT_SHEET)
    lists.append(["계정"])
    for a in accounts:
        lists.append([a])
    lists.column_dimensions["A"].width = 32
    dv = DataValidation(type="list", formula1=f"='{ACCOUNT_SHEET}'!$A$2:$A${len(accounts) + 1}",
                        allow_blank=True, showErrorMessage=True,
                        errorTitle="계정", error="목록에 있는 계정을 고르세요.")
    ex = DataValidation(type="list", formula1=f'"{EXCLUDE_MARK}"', allow_blank=True)
    ws.add_data_validation(dv)
    ws.add_data_validation(ex)
    dv.add(f"{col['확정 계정']}2:{col['확정 계정']}{last}")
    ex.add(f"{col['올리지 않음']}2:{col['올리지 않음']}{last}")

    widths = {"수임처": 18, "거래일자": 11, "거래처": 24, "합계": 11, "업태": 14, "종목": 16,
              "품명": 14, "유형": 6, "추천 계정": 22, "근거": 40, "확신": 6, "확정 계정": 26,
              "올리지 않음": 11, "메모": 20, "파일": 10, "행": 6}
    for name, w in widths.items():
        ws.column_dimensions[col[name]].width = w
    ws.column_dimensions[col["파일"]].hidden = True
    ws.column_dimensions[col["행"]].hidden = True
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{col['행']}{last}"

    how = wb.create_sheet(HOWTO_SHEET)
    for line in _HOWTO:
        how.append([line])
    how.column_dimensions["A"].width = 100
    how["A1"].font = Font(bold=True, size=13)

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError as e:
        raise DeskError("답지가 엑셀에서 열려 있습니다. 엑셀을 닫고 다시 눌러 주세요.") from e
    return path


# ── 처리하기 ──

@dataclass
class PrepareReport:
    files: int = 0
    failures: dict = field(default_factory=dict)
    rows: int = 0
    unresolved: int = 0        # 확정 계정이 빈 행
    ai_filled: int = 0         # 클로드 답으로 채운 행
    kept: int = 0              # 아버님이 이미 고친 칸을 살린 행
    questions: int = 0         # 클로드에게 새로 물을 것(가지)
    claude_notes: list = field(default_factory=list)
    sheet: Path | None = None


def prepare(base=None, *, config_dir: str | None = None) -> PrepareReport:
    from kafa.pipeline.ask import prepare_questions
    from kafa.recommend.host_picks import BASIS_PREFIX
    from kafa.store.db import VoucherStore

    paths = Paths(base_dir(base)).ensure()
    rep = PrepareReport(claude_notes=ingest_claude_answers(paths, config_dir=config_dir))
    names = _names(config_dir)
    rows: list[SheetRow] = []

    def on_file(file_rel, ctx, classified):
        for c in classified:
            r = sheet_row(ctx.client, file_rel, c, names)
            if r is not None:
                rows.append(r)

    res = prepare_questions(paths.inbox, paths.internal, config_dir=config_dir, on_file=on_file)
    rep.files, rep.failures, rep.questions = res.files, res.failures, res.new

    qpath = paths.claude / QUESTION_FILE
    if res.new:
        shutil.copyfile(res.path, qpath)
        write_prompt(paths)
    else:
        for stale in (qpath, paths.claude / PROMPT_FILE):
            if stale.exists():
                stale.unlink()

    rows.sort(key=lambda r: (r.client, r.date, r.vendor, r.row))
    old = find_sheet(paths)
    if old is not None:
        rep.kept = _keep_father_edits(rows, read_sheet(old))
    db = VoucherStore(paths.internal / "kafa.db")
    try:
        usage = db.account_usage()
    finally:
        db.close()
    rep.sheet = write_sheet(paths.sheet, rows, account_list(config_dir, usage))
    if old is not None and old != paths.sheet:
        old.unlink()                               # .xls 로 저장했던 것 — 새 답지로 대신한다
    rep.rows = len(rows)
    rep.unresolved = sum(1 for r in rows if not r.chosen and not r.exclude)
    rep.ai_filled = sum(1 for r in rows if r.basis.startswith(BASIS_PREFIX))
    return rep


# ── 업로드본 만들기 ──

@dataclass
class FinishReport:
    answered: int = 0
    excluded: int = 0
    result: object = None          # PipelineResult
    copied: list = field(default_factory=list)
    archived: Path | None = None


def _answers_from_sheet(rows: list[dict], config_dir) -> dict:
    from kafa.clients import resolve_account_text
    answers, errors = {}, []
    for r in rows:
        exclude = r["exclude"].lower() in _EXCLUDE_WORDS
        name = ""
        if r["chosen"] and not exclude:
            code, got = resolve_account_text(r["chosen"], config_dir)
            if code is None:
                errors.append(f"{r['sheet_row']}행 확정 계정 '{r['chosen']}': {got}")
                continue
            name = got
        if exclude or name:
            answers[(r["file"], r["row"])] = {"name": name, "exclude": exclude,
                                              "total": r["total"]}
    if errors:
        raise DeskError("답지에서 고칠 곳이 있습니다(업로드본을 만들지 않았습니다):\n  "
                        + "\n  ".join(errors[:15])
                        + (f"\n  … 외 {len(errors) - 15}건" if len(errors) > 15 else ""))
    return answers


def _same_amount(a: str, b) -> bool:
    try:
        return Decimal(str(a).replace(",", "")) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


def finish(base=None, *, config_dir: str | None = None) -> FinishReport:
    from kafa.pipeline.runner import run_pipeline

    paths = Paths(base_dir(base)).ensure()
    sheet = find_sheet(paths)
    if sheet is None:
        raise DeskError("답지가 없습니다. 먼저 '처리하기'를 눌러 답지를 만들어 주세요.")
    answers = _answers_from_sheet(read_sheet(sheet), config_dir)
    rep = FinishReport(answered=sum(1 for a in answers.values() if a["name"]),
                       excluded=sum(1 for a in answers.values() if a["exclude"]))

    def hook(file_rel, rows):
        for i, row in enumerate(rows):
            a = answers.get((file_rel, row.raw_index))
            if a is None:
                continue
            if a["total"] and not _same_amount(a["total"], row.합계):
                raise DeskError(f"{file_rel}: 답지와 파일 내용이 다릅니다(파일이 바뀐 것 같습니다). "
                                "'처리하기'를 다시 눌러 답지를 새로 만들어 주세요.")
            if a["exclude"]:
                rows[i] = replace(row, 전표상태=EXCLUDED_STATUS)
            else:
                rows[i] = replace(row, 차변계정=a["name"], 전표상태="")

    rep.result = run_pipeline(paths.inbox, paths.internal, row_hook=hook, config_dir=config_dir)
    for o in rep.result.outcomes:
        src = paths.internal / o.client / o.period
        dest = paths.uploads / o.client / o.period
        stem = Path(o.file).stem + "_upload"
        for f in sorted(src.glob(stem + "*")):
            if f.suffix.lower() == ".xls" or f.name.endswith(("_review.txt", "_review.csv",
                                                              "_대사.txt")):
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest / f.name)
                rep.copied.append(dest / f.name)
    if rep.result.ok:
        done = paths.sheets / DONE
        done.mkdir(exist_ok=True)
        rep.archived = done / f"답지_{_stamp()}{sheet.suffix}"
        try:
            shutil.move(str(sheet), str(rep.archived))
        except PermissionError:
            rep.archived = None                    # 엑셀에서 열려 있다 — 다음 '처리하기' 가 새로 만든다
    return rep


# ── 처음 한 번: 폴더·실행 아이콘 ──

_BAT = """@echo off
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"
"{python}" -m kafa.desk_cli {cmd} --base "%~dp0."
echo.
pause
"""


def setup(base=None, *, import_db=None, import_clients=None, python: str | None = None,
          force: bool = False) -> list[str]:
    """폴더를 만들고, 아드님 PC 의 처리 이력·수임처 속성을 옮기고, 더블클릭 아이콘(.bat)을 만든다."""
    from kafa.config_loader import load_clients, local_clients_path
    from kafa.store.db import VoucherStore

    paths = Paths(base_dir(base)).ensure()
    notes = [f"폴더: {paths.base}"]
    db = paths.internal / "kafa.db"
    if import_db:
        src = Path(import_db).expanduser()
        if not src.exists():
            raise DeskError(f"처리 이력 파일이 없습니다: {src}")
        if db.exists() and not force:
            notes.append("처리 이력: 이미 있어 그대로 둡니다(--force 로 덮어쓰기)")
        else:
            shutil.copy2(src, db)
            notes.append("처리 이력: 옮겼습니다")
    if import_clients:
        src = Path(import_clients).expanduser()
        if not src.exists():
            raise DeskError(f"수임처 속성 파일이 없습니다: {src}")
        dest = local_clients_path()
        if dest.exists() and not force:
            notes.append("수임처 속성: 이미 있어 그대로 둡니다(--force 로 덮어쓰기)")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            load_clients.cache_clear()
            notes.append(f"수임처 속성: 옮겼습니다 → {dest}")
    if db.exists():
        store = VoucherStore(db)
        try:
            clients = store.clients()
        finally:
            store.close()
        for cid in clients:
            (paths.inbox / cid).mkdir(exist_ok=True)
        notes.append(f"받은자료 폴더: 수임처 {len(clients)}곳")
    py = python or sys.executable
    for name, cmd in (("처리하기.bat", "prepare"), ("업로드본_만들기.bat", "finish")):
        (paths.base / name).write_text(_BAT.format(python=py, cmd=cmd), encoding="utf-8",
                                       newline="\r\n")
    notes.append("실행 아이콘: 처리하기.bat · 업로드본_만들기.bat")
    return notes
