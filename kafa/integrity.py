"""행 수·금액 대사 — 실행마다 맞춰 보고, 안 맞으면 그 업로드본은 쓰지 않는다.

2026-09 수집분에서 전표 169건 3,370,590원이 경고 없이 빠졌다(DB 키 충돌). 그 전에
787,590원이 안 맞는다는 신호가 있었는데 '표시 방식 차이'로 넘겼다. 사람이 알아채길
기대하지 않고 코드가 멈춘다. 근거: docs/domain_notes.md '행 수·금액 대사'.

맞춰 보는 것 — 전부 건수와 합계 금액 둘 다:
  1) 파일이 밝힌 총계(하단 '합계 : N건') = 읽은 행
  2) 읽은 행 = 분류된 행
  3) 분류된 행 = 업로드 + 스킵(사유별) + 따로 뺀 행
  4) 업로드 파일을 다시 읽은 것 = 업로드
  5) DB 에 실제 들어간 키 = 분류된 행(파이프라인만)

보고서에는 건수·금액·스킵 사유만 쓴다. 거래처·사업자번호는 담지 않는다(보안 제0원칙).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from kafa.config_loader import load_rules

_CENT = Decimal("0.01")
HOLD_SUFFIX = ".대사불일치"


@dataclass(frozen=True)
class Count:
    건수: int = 0
    금액: Decimal = Decimal(0)

    def __add__(self, other: "Count") -> "Count":
        return Count(self.건수 + other.건수, self.금액 + other.금액)

    def same(self, other: "Count") -> bool:
        return (self.건수 == other.건수
                and self.금액.quantize(_CENT) == other.금액.quantize(_CENT))

    def __str__(self) -> str:
        return f"{self.건수:,}건 {int(self.금액):,}원"


def _amount(value) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:                       # noqa: BLE001 — 대사 자체가 멈추면 안 된다
        return Decimal(0)


def _count(amounts) -> Count:
    total, n = Decimal(0), 0
    for a in amounts:
        total += _amount(a)
        n += 1
    return Count(n, total)


@dataclass
class Tally:
    """한 파일의 대사 결과."""
    입력: Count = field(default_factory=Count)
    분류: Count = field(default_factory=Count)
    업로드: Count = field(default_factory=Count)
    스킵: Count = field(default_factory=Count)
    스킵_사유: dict[str, Count] = field(default_factory=dict)
    보류: Count = field(default_factory=Count)
    미해소: Count = field(default_factory=Count)      # 업로드 중 계정이 빈 것(참고)
    파일_총계: Count | None = None
    업로드_파일: Count | None = None
    DB_적재: int | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def line(self) -> str:
        """한 줄 요약 — 파이프라인 출력용."""
        held = f" + 따로 뺀 {self.보류.건수}" if self.보류.건수 else ""
        head = (f"입력 {self.입력.건수} = 업로드 {self.업로드.건수} + 스킵 "
                f"{self.스킵.건수}{held}")
        if not self.ok:
            return f"대사 불일치 — {head} · " + " / ".join(self.problems)
        tail = " · 경고 " + " / ".join(self.warnings) if self.warnings else ""
        return f"대사 OK — {head}{tail}"


class IntegrityError(RuntimeError):
    """행 수·금액이 안 맞아 업로드본을 쓰지 않기로 한 경우."""

    def __init__(self, tally: Tally, report_path: Path | None = None):
        self.tally = tally
        self.report_path = report_path
        where = f" (자세히: {report_path.name})" if report_path else ""
        super().__init__(tally.line() + where)


def _policy(config_dir: str | None) -> dict:
    return (load_rules(config_dir).get("integrity") or {})


def _flag(t: Tally, level: str, message: str) -> None:
    if level == "fail":
        t.problems.append(message)
    elif level == "warn":
        t.warnings.append(message)


def tally_batch(rows, classified, out_rows, *, held=(), config_dir: str | None = None) -> Tally:
    """읽은 행 → 분류 → 업로드/스킵/뺀 행 까지 (1~3)."""
    policy = _policy(config_dir)
    t = Tally()
    t.입력 = _count(r.합계 for r in rows)
    t.분류 = _count(c.source.합계 if c.source is not None else 0 for c in classified)
    t.업로드 = _count(o.합계 for o in out_rows)
    skipped = [c for c in classified if c.skipped]
    t.스킵 = _count(c.source.합계 if c.source is not None else 0 for c in skipped)
    for c in skipped:
        reason = (c.skip_reason or "사유 없음").strip() or "사유 없음"
        one = Count(1, _amount(c.source.합계 if c.source is not None else 0))
        t.스킵_사유[reason] = t.스킵_사유.get(reason, Count()) + one
    t.보류 = _count(c.source.합계 if c.source is not None else 0 for c in held)
    t.미해소 = _count(o.합계 for o in out_rows
                    if o.차변계정코드 in (None, ""))

    declared_count = getattr(rows, "declared_count", None)
    if declared_count is not None:
        declared_amount = getattr(rows, "declared_amount", None)
        t.파일_총계 = Count(int(declared_count), _amount(declared_amount))
        if t.파일_총계.건수 != t.입력.건수:
            _flag(t, str(policy.get("declared_count", "fail")),
                  f"파일 하단 총계 {t.파일_총계.건수:,}건인데 읽은 행은 {t.입력.건수:,}건")
        elif declared_amount is not None and not t.파일_총계.same(t.입력):
            _flag(t, str(policy.get("declared_amount", "warn")),
                  f"파일 하단 총계 금액 {int(t.파일_총계.금액):,}원인데 "
                  f"읽은 행 합계는 {int(t.입력.금액):,}원")

    if not t.분류.same(t.입력):
        t.problems.append(f"읽은 행 {t.입력}인데 분류된 행은 {t.분류}")
    나간 = t.업로드 + t.스킵 + t.보류
    if not 나간.same(t.분류):
        t.problems.append(f"분류 {t.분류}인데 업로드+스킵+뺀 행은 {나간}")
    return t


def read_back(files) -> Count:
    """업로드 .xls 를 다시 읽어 건수·합계를 센다(분할 파일 전부)."""
    import xlrd

    from kafa.io_wehago.schema import OUTPUT_COLUMNS
    col = OUTPUT_COLUMNS.index("합계")
    total = Count()
    for f in files:
        sheet = xlrd.open_workbook(str(f)).sheet_by_index(0)
        total = total + _count(sheet.cell_value(i, col) for i in range(1, sheet.nrows))
    return total


def check_upload_files(t: Tally, files) -> Tally:
    """(4) 쓴 파일을 다시 읽어 업로드 건수·금액과 맞춘다."""
    t.업로드_파일 = read_back(files)
    if not t.업로드_파일.same(t.업로드):
        t.problems.append(f"업로드 {t.업로드}인데 파일을 다시 읽으니 {t.업로드_파일}")
    return t


def check_store(t: Tally, keys: list[str], present: int) -> Tally:
    """(5) DB 적재 — 키가 겹치거나 빠지면 그만큼 전표가 사라진 것이다."""
    distinct = len(set(keys))
    t.DB_적재 = present
    if distinct != t.분류.건수:
        t.problems.append(f"분류 {t.분류.건수:,}건인데 DB 키는 {distinct:,}개 "
                          f"({t.분류.건수 - distinct:,}건이 같은 키로 겹침)")
    if present != distinct:
        t.problems.append(f"DB 키 {distinct:,}개 중 실제 적재 {present:,}개")
    return t


def hold(files) -> list[Path]:
    """대사가 깨진 업로드본은 올리지 못하게 확장자를 바꿔 보류한다."""
    held = []
    for f in files:
        p = Path(f)
        if p.exists():
            target = p.with_name(p.name + HOLD_SUFFIX)
            p.replace(target)
            held.append(target)
    return held


def render(t: Tally, *, held: bool = False) -> str:
    """담당자용 대사표 — 건수·금액·스킵 사유만(실명 없음).

    held: 업로드본을 쓴 뒤에 깨져 보류했는지(True), 아예 쓰지 않았는지(False).
    """
    lines = ["행 수·금액 대사", "=" * 40, ""]
    if t.파일_총계 is not None:
        lines.append(f"파일이 밝힌 총계   {t.파일_총계}")
    lines += [f"읽은 행            {t.입력}",
              f"분류된 행          {t.분류}",
              f"  업로드           {t.업로드}   (그중 계정 빈 것 {t.미해소})",
              f"  스킵             {t.스킵}"]
    for reason, c in sorted(t.스킵_사유.items()):
        lines.append(f"    {reason:<14} {c}")
    if t.보류.건수:
        lines.append(f"  따로 뺀 행       {t.보류}")
    if t.업로드_파일 is not None:
        lines.append(f"업로드 파일 재확인  {t.업로드_파일}")
    if t.DB_적재 is not None:
        lines.append(f"DB 적재 확인       {t.DB_적재:,}건")
    verdict = ("일치" if t.ok else
               "불일치 — 업로드본을 보류했습니다" if held else
               "불일치 — 업로드본을 만들지 않았습니다")
    lines += ["", "결과: " + verdict]
    lines += [f"  ✗ {p}" for p in t.problems]
    lines += [f"  △ {w}" for w in t.warnings]
    if not t.ok and held:
        lines += ["", f"보류된 파일은 끝에 '{HOLD_SUFFIX}' 가 붙어 있어 위하고에 올라가지 않습니다.",
                  "원인을 찾기 전에는 이 파일을 올리지 마세요."]
    if not t.ok:
        lines += ["원본은 그 자리에 남아 있습니다 — 원인을 고친 뒤 다시 돌리면 됩니다."]
    return "\n".join(lines) + "\n"
