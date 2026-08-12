"""Phase 3 — 업로드 양식(.xls) 생성. xlwt 사용(openpyxl은 .xls 쓰기 불가).

필수값(거래일자·합계) 검증, CP949 인코딩 안전화, 2MB 초과 시 행 단위 자동 분할,
중복전표/스킵 제외.
[보류] 거래구분 허용값(config 기본 공란), 봉사료=비과세 동일성.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from kafa.config_loader import load_rules
from kafa.io_wehago.schema import OUTPUT_COLUMNS, OUTPUT_HEADERS, REQUIRED_OUTPUT
from kafa.rules.models import ClassifiedRow, Deduct

DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2MB


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
    거래구분: str = ""       # 🤖 허용값 미확정([보류]) → config 기본(공란)

    def as_list(self) -> list:
        return [getattr(self, c) for c in OUTPUT_COLUMNS]


def _cp949_safe(text: str) -> str:
    """CP949로 인코딩 불가한 문자를 '?'로 치환(업로드 안전화)."""
    try:
        text.encode("cp949")
        return text
    except UnicodeEncodeError:
        return text.encode("cp949", errors="replace").decode("cp949")


def to_output_row(cls: ClassifiedRow, *, config_dir: str | None = None) -> OutputRow:
    """ClassifiedRow(+source) → 업로드 양식 행."""
    src = cls.source
    out_cfg = load_rules(config_dir).get("output", {}) or {}
    공제_map = {
        Deduct.DEDUCTIBLE: "공제",
        Deduct.NON_DEDUCTIBLE: "불공제",
        Deduct.REVIEW: "검토",
    }
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
        공제여부=공제_map.get(cls.공제여부, ""),
        거래구분=str(out_cfg.get("transaction_type_default", "") or ""),
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


def _render_xls(rows: list[OutputRow]) -> bytes:
    """행 묶음을 .xls 바이트로 렌더(메모리). 크기 측정·분할 판단에 사용."""
    import xlwt

    wb = xlwt.Workbook(encoding="cp949")
    ws = wb.add_sheet("신용카드매입")
    for c, name in enumerate(OUTPUT_HEADERS):   # 실제 양식 헤더 문구 그대로
        ws.write(0, c, name)
    for i, r in enumerate(rows, start=1):
        for c, val in enumerate(r.as_list()):
            if val is None or val == "":
                ws.write(i, c, "")
            elif isinstance(val, str):
                ws.write(i, c, _cp949_safe(val))
            else:
                ws.write(i, c, float(val))
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _split_to_fit(rows: list[OutputRow], max_bytes: int) -> list[tuple[list[OutputRow], bytes]]:
    """각 묶음이 max_bytes 이하가 되도록 행을 이분 분할(단일 행은 그대로)."""
    data = _render_xls(rows)
    if len(data) <= max_bytes or len(rows) <= 1:
        return [(rows, data)]
    mid = len(rows) // 2
    return _split_to_fit(rows[:mid], max_bytes) + _split_to_fit(rows[mid:], max_bytes)


def write_upload_xls(
    rows: list[OutputRow],
    path: str | Path,
    *,
    strict: bool = True,
    max_bytes: int | None = None,
    config_dir: str | None = None,
) -> list[Path]:
    """업로드용 .xls 작성. 2MB 초과 시 _part01.xls 형태로 분할. 반환: 작성 파일 목록."""
    if max_bytes is None:
        out_cfg = load_rules(config_dir).get("output", {}) or {}
        max_bytes = int(out_cfg.get("max_bytes", DEFAULT_MAX_BYTES))

    errs = validate_required(rows)
    if errs and strict:
        raise ValueError(f"필수값 누락 {len(errs)}건 (거래일자/합계): 예) {errs[:3]}")

    chunks = _split_to_fit(rows, max_bytes)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    if len(chunks) == 1:
        out.write_bytes(chunks[0][1])
        written.append(out)
    else:
        for i, (_, data) in enumerate(chunks, start=1):
            part = out.with_name(f"{out.stem}_part{i:02d}{out.suffix}")
            part.write_bytes(data)
            written.append(part)
    return written
