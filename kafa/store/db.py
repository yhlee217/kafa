"""SQLite 베이스 데이터 저장소.

멱등성: voucher_key = hash(고객 + 거래일자 + 거래처 + 사업자번호 + 합계 + 품명).
INSERT OR IGNORE 로 같은 키는 재적재해도 중복되지 않고 기존 레코드를 보존한다
(재처리 시 좋은 분류가 덮어써지지 않음 — first-wins).

보안 제0원칙: DB 는 로컬 전용. 금액은 Decimal 문자열로 보관(부동소수 오차 방지).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kafa.rules.models import ClassifiedRow
from kafa.security import hash_id

_DDL = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    name       TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS vouchers (
    voucher_key  TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    period       TEXT,
    거래일자      TEXT,
    거래처        TEXT,
    사업자번호    TEXT,
    품명          TEXT,
    공급가액      TEXT,
    세액          TEXT,
    비과세        TEXT,
    합계          TEXT,
    유형코드      INTEGER,
    차변계정코드  INTEGER,
    대변계정코드  INTEGER,
    공제여부      TEXT,
    판정유형      TEXT,
    신뢰도        REAL,
    추천근거      TEXT,
    skipped       INTEGER,
    skip_reason   TEXT,
    source_file   TEXT,
    ingested_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_vouchers_client_period ON vouchers(client_id, period);
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT,
    started_at TEXT,
    inbox      TEXT,
    files      INTEGER,
    written    INTEGER,
    skipped    INTEGER,
    failures   INTEGER
);
"""

_COLUMNS = [
    "client_id", "period", "거래일자", "거래처", "사업자번호", "품명",
    "공급가액", "세액", "비과세", "합계", "유형코드", "차변계정코드", "대변계정코드",
    "공제여부", "판정유형", "신뢰도", "추천근거", "skipped", "skip_reason", "source_file",
]
_INSERT_SQL = (
    "INSERT OR IGNORE INTO vouchers(voucher_key," + ",".join(_COLUMNS) + ",ingested_at) "
    "VALUES(?," + ",".join("?" * len(_COLUMNS)) + ",datetime('now'))"
)


@dataclass
class IngestResult:
    inserted: int = 0   # 새로 적재
    existing: int = 0    # 키 이미 존재 → 무시(멱등)


def _voucher_key(client_id: str, c: ClassifiedRow) -> str:
    s = c.source
    parts = [client_id]
    if s is not None:
        parts += [f"{s.연도}-{s.일자}", s.거래처, s.사업자등록번호, str(s.합계), s.품명]
    return hash_id("|".join(parts), salt="voucher", length=20)


def _to_row(client_id: str, period: str, c: ClassifiedRow, source_file: str) -> tuple:
    s = c.source
    공제 = c.공제여부.value if c.공제여부 else None
    판정 = c.판정유형.value if c.판정유형 else None
    return (
        client_id, period,
        f"{s.연도}-{s.일자}" if s else "",
        s.거래처 if s else "",
        s.사업자등록번호 if s else "",
        s.품명 if s else "",
        str(s.공급가액) if s else "0",
        str(s.세액) if s else "0",
        str(s.비과세) if s else "0",
        str(s.합계) if s else "0",
        c.유형코드, c.차변계정코드, c.대변계정코드,
        공제, 판정, c.신뢰도, c.추천근거,
        1 if c.skipped else 0, c.skip_reason or "",
        source_file,
    )


class VoucherStore:
    """SQLite 거래 누적 저장소. 컨텍스트 매니저 지원."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_DDL)
        self._conn.commit()

    def __enter__(self) -> "VoucherStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def upsert_client(self, client_id: str, name: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO clients(client_id,name,created_at) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(client_id) DO UPDATE SET name=COALESCE(excluded.name,name)",
            (client_id, name))
        self._conn.commit()

    def upsert_vouchers(self, client_id: str, period: str,
                        rows: list[ClassifiedRow], *, source_file: str = "") -> IngestResult:
        self.upsert_client(client_id)   # 거래가 있는 고객은 항상 등록
        res = IngestResult()
        for c in rows:
            key = _voucher_key(client_id, c)
            cur = self._conn.execute(_INSERT_SQL,
                                     (key, *_to_row(client_id, period, c, source_file)))
            if cur.rowcount == 1:
                res.inserted += 1
            else:
                res.existing += 1
        self._conn.commit()
        return res

    def count(self, client_id: str | None = None) -> int:
        if client_id is not None:
            return self._conn.execute(
                "SELECT COUNT(*) FROM vouchers WHERE client_id=?", (client_id,)).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0]

    def clients(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT client_id FROM clients ORDER BY client_id")]

    def record_run(self, run_id: str, inbox, files: int, written: int,
                   skipped: int, failures: int) -> None:
        self._conn.execute(
            "INSERT INTO runs(run_id,started_at,inbox,files,written,skipped,failures) "
            "VALUES(?,datetime('now'),?,?,?,?,?)",
            (run_id, str(inbox), files, written, skipped, failures))
        self._conn.commit()
