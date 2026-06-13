"""Phase 3 (골격) — 업로드 양식(.xls) 생성. xlwt 사용(openpyxl은 .xls 쓰기 불가).

필수값(거래일자·합계) 검증, CP949 인코딩, 중복전표/스킵 제외.
[보류/TODO] 2MB 초과 시 행 단위 분할, 거래구분 허용값, 봉사료=비과세 동일성.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kafa.io_wehago.schema import OUTPUT_COLUMNS, REQUIRED_OUTPUT
from kafa.rules.models import ClassifiedRow, Deduct


@dataclass
class OutputRow:
    거래일자: str = ""
    거래처: str = ""
    사업자번호: str = ""
    품명: str = ""
    유형: object = ""        # 유형코드
    공급가액: object = ""
    세액: object = ""
    봉사료: object = ""      # 🤖 비과세 칸으로 추정([보류])
    합계: object = ""
    차변계정코드: object = ""
    대변계정코드: object = ""
    공제여부: str = ""
    거래구분: str = ""       # 🤖 허용값 미확정([보류]) → 공란 기본

    def as_list(self) -> list:
        return [getattr(self, c) for c in OUTPUT_COLUMNS]


def to_output_row(cls: ClassifiedRow) -> OutputRow:
    """ClassifiedRow(+source) → 업로드 양식 행."""
    src = cls.source
    공제 = ""
    if cls.공제여부 == Deduct.DEDUCTIBLE:
        공제 = "공제"
    elif cls.공제여부 == Deduct.NON_DEDUCTIBLE:
        공제 = "불공제"
    elif cls.공제여부 == Deduct.REVIEW:
        공제 = "검토"
    거래일자 = f"{src.연도}-{src.일자}".strip("-") if src else ""
    return OutputRow(
        거래일자=거래일자,
        거래처=src.거래처 if src else "",
        사업자번호=src.사업자등록번호 if src else "",
        품명=src.품명 if src else "",
        유형=cls.유형코드 if cls.유형코드 is not None else "",
        공급가액=src.공급가액 if src else "",
        세액=src.세액 if src else "",
        봉사료=src.비과세 if src else "",
        합계=src.합계 if src else "",
        차변계정코드=cls.차변계정코드 if cls.차변계정코드 is not None else "",
        대변계정코드=cls.대변계정코드 if cls.대변계정코드 is not None else "",
        공제여부=공제,
        거래구분="",   # [보류]
    )


def validate_required(rows: list[OutputRow]) -> list[tuple[int, str]]:
    """필수값 누락 검출. 반환: (행번호, 누락필드) 목록."""
    errs: list[tuple[int, str]] = []
    for i, r in enumerate(rows):
        for field in REQUIRED_OUTPUT:
            val = getattr(r, field, "")
            if val in (None, "") or (isinstance(val, str) and not val.strip()):
                errs.append((i, field))
    return errs


def write_upload_xls(rows: list[OutputRow], path: str | Path,
                     *, strict: bool = True) -> Path:
    """업로드용 .xls 작성. strict면 필수값 누락 시 예외."""
    import xlwt

    errs = validate_required(rows)
    if errs and strict:
        raise ValueError(f"필수값 누락 {len(errs)}건 (거래일자/합계): 예) {errs[:3]}")

    wb = xlwt.Workbook(encoding="cp949")
    ws = wb.add_sheet("신용카드매입")
    for c, name in enumerate(OUTPUT_COLUMNS):
        ws.write(0, c, name)
    for i, r in enumerate(rows, start=1):
        for c, val in enumerate(r.as_list()):
            ws.write(i, c, "" if val is None else (float(val)
                     if hasattr(val, "__float__") and not isinstance(val, str) else str(val)))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    # TODO: 2MB 초과 시 행 단위 자동 분할(여러 .xls).
    return out
