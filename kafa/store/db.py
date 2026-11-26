"""SQLite 베이스 데이터 저장소.

멱등성: voucher_key = hash(고객 + 거래일자 + 거래처 + 사업자번호 + 합계 + 품명 [+ 순번]).
INSERT OR IGNORE 로 같은 키는 재적재해도 중복되지 않고 기존 레코드를 보존한다
(재처리 시 좋은 분류가 덮어써지지 않음 — first-wins).

**순번이 필요한 이유**: 같은 날 같은 가맹점에서 같은 금액·품명을 두 번 결제하는 일은
실제로 일어난다(편의점 두 번, 주유 두 번). 순번이 없으면 두 번째 행이 첫 번째와 같은
키가 되어 INSERT OR IGNORE 에 조용히 버려진다. 더 나쁜 건 first-wins 라서, 위하고가
'중복전표'로 표시한 행이 먼저 들어오면 **정상 행이 영영 가려진다**.
순번은 같은 배치 안에서 키가 겹치는 행에만 붙고, 같은 파일을 다시 넣으면 같은 순서로
같은 순번이 나오므로 멱등성은 그대로다. 첫 행은 순번을 붙이지 않아 기존 DB 와 호환된다.

**확정출처(누가 정했나)**: 받은 자료의 계정·유형은 대부분 위하고 자동 분류(전표상태 '확정가능')이고
담당자가 확정한 행은 '전표확정' 뿐이었다(2026-11-26 실측, docs/domain_notes.md). 그래서 행마다
`확정출처` 를 남긴다 — 담당자(kafa-desk 답지) / 전표확정(위하고) / 위하고(자동 분류) / 규칙 / 추천.
유형 추천은 담당자 쪽만 배우고, 계정 이력은 같은 가맹점에 담당자 답이 있으면 그것만 쓴다.

보안 제0원칙: DB 는 로컬 전용. 금액은 Decimal 문자열로 보관(부동소수 오차 방지).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from kafa.rules.models import ClassifiedRow, Verdict
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
    업태          TEXT,
    종목          TEXT,
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
    ingested_at   TEXT,
    전표상태      TEXT,
    확정출처      TEXT,
    원유형        TEXT
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
    "client_id", "period", "거래일자", "거래처", "사업자번호", "품명", "업태", "종목",
    "공급가액", "세액", "비과세", "합계", "유형코드", "차변계정코드", "대변계정코드",
    "공제여부", "판정유형", "신뢰도", "추천근거", "skipped", "skip_reason", "source_file",
    "전표상태", "확정출처", "원유형",
]

# 확정출처 — 담당자 판단으로 치는 것과 아닌 것
SOURCE_DESK = "담당자"        # kafa-desk 답지에서 아버님이 정한 행
SOURCE_CONFIRMED = "전표확정"  # 위하고에서 담당자가 확정·전송한 행
SOURCE_WEHAGO = "위하고"       # 위하고가 채워 둔 그대로(확정가능) — 자동 분류
SOURCE_RULE = "규칙"           # kafa 규칙이 정함(예: 교통 대행사 자동 확정)
SOURCE_RECO = "추천"           # kafa 추천(이력·시드·Claude)
FATHER_SOURCES = (SOURCE_DESK, SOURCE_CONFIRMED)
_INSERT_SQL = (
    "INSERT OR IGNORE INTO vouchers(voucher_key," + ",".join(_COLUMNS) + ",ingested_at) "
    "VALUES(?," + ",".join("?" * len(_COLUMNS)) + ",datetime('now'))"
)


@dataclass
class IngestResult:
    inserted: int = 0   # 새로 적재
    existing: int = 0    # 키 이미 존재 → 무시(멱등)
    upgraded: int = 0    # 스킵으로 들어가 있던 걸 정상 분류로 되살림
    keys: list[str] = field(default_factory=list)   # 이번 배치가 쓴 키(대사용)


def _voucher_key(client_id: str, c: ClassifiedRow, occurrence: int = 0) -> str:
    """전표 한 줄의 키. occurrence 는 같은 배치에서 앞에 나온 동일 행의 수."""
    s = c.source
    parts = [client_id]
    if s is not None:
        parts += [f"{s.연도}-{s.일자}", s.거래처, s.사업자등록번호, str(s.합계), s.품명]
    if occurrence:
        parts.append(f"#{occurrence}")   # 첫 행은 붙이지 않는다(기존 DB 호환)
    return hash_id("|".join(parts), salt="voucher", length=20)


def status_settings(config_dir: str | None = None) -> tuple[frozenset[str], str]:
    """(담당자가 확정한 위하고 전표상태들, 답지로 정한 행에 붙이는 전표상태) — config `confirm_source`."""
    from kafa.config_loader import load_rules
    cfg = load_rules(config_dir).get("confirm_source") or {}
    return (frozenset(str(x) for x in (cfg.get("confirmed_status") or ["전표확정"])),
            str(cfg.get("desk_status") or "담당자확정"))


def confirm_source(c: ClassifiedRow, config_dir: str | None = None) -> str:
    """이 행의 계정을 누가 정했나. 계정이 없거나 스킵이면 빈 문자열."""
    if c.skipped or c.차변계정코드 is None:
        return ""
    s = c.source
    status = (s.전표상태 or "").strip() if s is not None else ""
    confirmed, desk = status_settings(config_dir)
    if status == desk:
        return SOURCE_DESK
    if status in confirmed:
        return SOURCE_CONFIRMED
    if s is not None and (s.차변계정 or "").strip() and c.판정유형 == Verdict.RULE_CONFIRMED:
        return SOURCE_WEHAGO
    return SOURCE_RULE if c.판정유형 == Verdict.RULE_CONFIRMED else SOURCE_RECO


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
        s.업태 if s else "",
        s.종목 if s else "",
        str(s.공급가액) if s else "0",
        str(s.세액) if s else "0",
        str(s.비과세) if s else "0",
        str(s.합계) if s else "0",
        c.유형코드, c.차변계정코드, c.대변계정코드,
        공제, 판정, c.신뢰도, c.추천근거,
        1 if c.skipped else 0, c.skip_reason or "",
        source_file,
        (s.전표상태 or "").strip() if s else "",
        confirm_source(c),
        (s.유형 or "").strip() if s else "",     # 원본 유형 칸 — 유형코드는 kafa 판정 결과다
    )


class VoucherStore:
    """SQLite 거래 누적 저장소. 컨텍스트 매니저 지원."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_DDL)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """기존 DB에 없는 컬럼을 추가(구버전 파일 호환)."""
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(vouchers)")}
        for col in ("업태", "종목", "전표상태", "확정출처", "원유형"):
            if col not in have:
                self._conn.execute(f"ALTER TABLE vouchers ADD COLUMN {col} TEXT")
        # 확정출처가 생기기 전 행: 계정이 채워져 들어온 처리 행은 위하고 자동 분류로 본다
        # (그때는 kafa-desk 를 쓰기 전이었다). 전표확정은 원본으로 되살린다(kafa.store.backfill).
        if "확정출처" not in have:
            self._conn.execute(
                "UPDATE vouchers SET 확정출처=CASE WHEN 판정유형='rule_confirmed' THEN ? "
                "ELSE ? END WHERE 차변계정코드 IS NOT NULL AND skipped=0",
                (SOURCE_WEHAGO, SOURCE_RECO))

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
        seen: dict[str, int] = {}
        for c in rows:
            base = _voucher_key(client_id, c)
            occurrence = seen.get(base, 0)
            seen[base] = occurrence + 1
            key = _voucher_key(client_id, c, occurrence)
            res.keys.append(key)
            cur = self._conn.execute(_INSERT_SQL,
                                     (key, *_to_row(client_id, period, c, source_file)))
            if cur.rowcount == 1:
                res.inserted += 1
            elif not c.skipped and self._is_stored_as_skipped(key):
                # first-wins 는 '좋은 분류를 지키려는' 규칙이다. 스킵된 행이 정상 행을
                # 가리는 건 그 취지에 어긋나므로, 이 방향으로만 덮어쓴다.
                self._conn.execute(
                    "UPDATE vouchers SET " + ",".join(f"{col}=?" for col in _COLUMNS)
                    + ",ingested_at=datetime('now') WHERE voucher_key=?",
                    (*_to_row(client_id, period, c, source_file), key))
                res.upgraded += 1
            else:
                res.existing += 1
        self._conn.commit()
        return res

    def count_keys(self, keys: list[str]) -> int:
        """이 키들 중 DB 에 실제로 있는 수 — 적재 대사용(kafa/integrity.py)."""
        unique = list(dict.fromkeys(keys))
        found = 0
        for i in range(0, len(unique), 500):          # SQLite 변수 개수 한도 회피
            chunk = unique[i:i + 500]
            found += self._conn.execute(
                "SELECT COUNT(*) FROM vouchers WHERE voucher_key IN ("
                + ",".join("?" * len(chunk)) + ")", chunk).fetchone()[0]
        return found

    def _is_stored_as_skipped(self, key: str) -> bool:
        row = self._conn.execute(
            "SELECT skipped FROM vouchers WHERE voucher_key=?", (key,)).fetchone()
        return bool(row and row[0])

    def type_records(self, client_id: str | None = None,
                     *, exclude_source: str | None = None,
                     config_dir: str | None = None) -> list[tuple]:
        """유형 추천의 재료 [(거래처, 사업자번호, 유형 라벨), ...].

        **담당자가 정한 행만**(확정출처 담당자·전표확정). 받은 자료의 유형은 위하고 기본값
        (≈ 국세청)이라 배우면 기본값을 되풀이할 뿐이다(2026-11-26 실측: 담당자가 확정한 행은
        93% 카과, 확정 전 행은 90% 일반). 도구가 추천한 행도 배우지 않는다. PII 포함 — 로컬 전용.
        """
        from kafa.config_loader import load_rules
        labels = {int(v): str(k) for k, v in
                  (load_rules(config_dir).get("vat_type_codes") or {}).items()}
        # 원본 유형 칸(원유형)이 담당자 판단이다. 유형코드는 적재 당시 kafa 판정이라 예전 기준
        # (국세청 칸)으로 들어간 행은 다르다(2026-11-26 실측). 원유형이 없는 행만 유형코드로.
        sql = ("SELECT 거래처, 사업자번호, 유형코드, 원유형 FROM vouchers "
               "WHERE (유형코드 IS NOT NULL OR COALESCE(원유형,'') <> '') AND skipped=0 "
               "AND 확정출처 IN (?,?)")
        params: list = list(FATHER_SOURCES)
        if client_id is not None:
            sql += " AND client_id=?"
            params.append(client_id)
        if exclude_source:
            sql += " AND COALESCE(source_file,'') <> ?"
            params.append(exclude_source)
        return [(r[0] or "", r[1] or "",
                 (r[3] or "").strip() or (labels.get(int(r[2]), "") if r[2] is not None else ""))
                for r in self._conn.execute(sql, params)]

    def account_records(self, *, config_dir: str | None = None,
                        father_only: bool = False) -> list[tuple]:
        """처리된 행 [(거래처, 업태, 종목, 품명, 유형 라벨, 차변계정코드), ...].

        Claude 추천 시험(kafa-ask exam)의 재료 — 위하고·담당자가 정한 계정이 정답이다.
        거래처는 로컬에서 대행사를 거르고 품명의 가맹점명을 가리는 데만 쓴다(PII — 밖으로
        내보내지 않는다).
        """
        from kafa.config_loader import load_rules
        labels = {int(v): str(k) for k, v in
                  (load_rules(config_dir).get("vat_type_codes") or {}).items()}
        sql = ("SELECT 거래처, 업태, 종목, 품명, 유형코드, 차변계정코드 FROM vouchers "
               "WHERE 차변계정코드 IS NOT NULL AND skipped=0 AND 판정유형='rule_confirmed'")
        params: list = []
        if father_only:          # 담당자가 정한 행만 — 그 밖은 위하고 자동 분류다
            sql += " AND 확정출처 IN (?,?)"
            params += list(FATHER_SOURCES)
        return [(r[0] or "", r[1] or "", r[2] or "", r[3] or "",
                 labels.get(int(r[4]), "") if r[4] is not None else "", int(r[5]))
                for r in self._conn.execute(sql, params)]

    def prior_records(self) -> list[tuple]:
        """사무실 전체 이력 [(client_id, 거래처, 사업자번호, 업태, 종목, 차변계정코드)].

        처리된 행(rule_confirmed)만 — 도구가 추천한 행을 다시 배우지 않는다. 거래처·사업자번호는
        대행사 거르기와 '처음 보는 가맹점' 세기에만 로컬에서 쓴다(PII — 밖으로 내보내지 않는다).
        """
        sql = ("SELECT client_id, 거래처, 사업자번호, 업태, 종목, 차변계정코드 FROM vouchers "
               "WHERE 차변계정코드 IS NOT NULL AND skipped=0 AND 판정유형='rule_confirmed'")
        return [(r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "", int(r[5]))
                for r in self._conn.execute(sql)]

    def unresolved_records(self) -> list[tuple]:
        """지금 미해소인 행 [(client_id, 거래처, 업태, 종목)] — 채울 수 있는 건수 측정용."""
        sql = ("SELECT client_id, 거래처, 업태, 종목 FROM vouchers WHERE 차변계정코드 IS NULL "
               "AND skipped=0 AND 판정유형='unresolved'")
        return [(r[0], r[1] or "", r[2] or "", r[3] or "") for r in self._conn.execute(sql)]

    def account_usage(self) -> dict[int, int]:
        """처리된 행의 차변계정 사용 횟수 {코드: 건수} — 이 사무실이 실제로 쓰는 계정."""
        sql = ("SELECT 차변계정코드, COUNT(*) FROM vouchers WHERE 차변계정코드 IS NOT NULL "
               "AND skipped=0 AND 판정유형='rule_confirmed' GROUP BY 차변계정코드")
        return {int(c): int(n) for c, n in self._conn.execute(sql)}

    def count(self, client_id: str | None = None) -> int:
        if client_id is not None:
            return self._conn.execute(
                "SELECT COUNT(*) FROM vouchers WHERE client_id=?", (client_id,)).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0]

    def clients(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT client_id FROM clients ORDER BY client_id")]

    def seed_records(self, client_id: str | None = None,
                     *, exclude_source: str | None = None) -> list[tuple]:
        """누적 이력 → 시드 레코드 [(거래처, 사업자번호, 차변계정코드), ...].

        추천(자가 시딩)의 재료. 같은 고객이 지난 달들에 어떤 가맹점을 어떤 계정으로
        처리했는지가 이번 달 미추천 해소의 가장 강한 근거다. 스킵·계정 미정 행은 제외.
        exclude_source: 지금 처리 중인 파일을 제외(재처리 시 자기 자신 참조 방지).

        Claude 가 추정한 행(근거 `AI(Claude) 추정`)은 뺀다 — 이번 달의 추정이 다음 달엔
        '같은 가맹점 이력'으로 둔갑해 더 높은 신뢰도로 되돌아오면 안 된다. 같은 특징이면 같은
        답이 다시 적용되므로(AI 로 표시된 채) 빼도 결과는 같고, 표시만 정직해진다.

        주의: 반환값에 거래처·사업자번호(PII)가 포함된다. **로컬 처리 전용**이며
        모델·리포트로 내보내지 않는다(시드는 코드가 계정코드만 뽑아 쓴다).
        """
        sql = ("SELECT 거래처, 사업자번호, 차변계정코드, 업태, 종목, client_id, 확정출처 FROM vouchers "
               "WHERE 차변계정코드 IS NOT NULL AND skipped=0 "
               "AND COALESCE(추천근거,'') NOT LIKE 'AI(Claude) 추정%'")
        params: list = []
        if client_id is not None:
            sql += " AND client_id=?"
            params.append(client_id)
        if exclude_source:
            sql += " AND COALESCE(source_file,'') <> ?"
            params.append(exclude_source)
        rows = self._conn.execute(sql, params).fetchall()
        # 같은 수임처·같은 가맹점에 담당자가 정한 행이 있으면 그것만 쓴다 — 위하고 자동 분류보다
        # 담당자 판단이 이긴다(가맹점은 사업자번호, 없으면 이름으로 가른다).
        father = {(r[5], r[1] or r[0]) for r in rows if r[6] in FATHER_SOURCES}
        return [(r[0] or "", r[1] or "", int(r[2]), r[3] or "", r[4] or "")
                for r in rows
                if r[6] in FATHER_SOURCES or (r[5], r[1] or r[0]) not in father]

    def backfill_status(self, client_id: str, rows: list) -> int:
        """원본 행(InputRow, 파일 순서 그대로)의 전표상태·원본 유형을 이미 쌓인 행에 채운다.

        확정출처·원유형이 생기기 전에 쌓인 행은 위하고가 확정한 것('전표확정')인지, 원본 유형 칸이
        무엇이었는지 DB 만으로는 알 수 없다(유형코드는 적재 당시 kafa 판정). 적재 때와 같은 키(같은
        순번 규칙)로 찾아 넣고, 담당자가 확정한 상태면 확정출처를 전표확정으로 바꾼다. 담당자 답지로
        정한 행의 확정출처는 건드리지 않는다. 바꾼 행 수.
        """
        confirmed, _ = status_settings()
        seen: dict[str, int] = {}
        changed = 0
        for r in rows:
            c = ClassifiedRow(source=r)
            base = _voucher_key(client_id, c)
            occurrence = seen.get(base, 0)
            seen[base] = occurrence + 1
            key = _voucher_key(client_id, c, occurrence)
            status = (r.전표상태 or "").strip()
            vat = (r.유형 or "").strip()
            cur = self._conn.execute(
                "UPDATE vouchers SET 전표상태=?, 원유형=?, 확정출처=CASE "
                "WHEN ? AND 차변계정코드 IS NOT NULL AND skipped=0 "
                "AND COALESCE(확정출처,'') <> ? THEN ? ELSE 확정출처 END "
                "WHERE voucher_key=? AND (COALESCE(전표상태,'') <> ? OR COALESCE(원유형,'') <> ?)",
                (status, vat, 1 if status in confirmed else 0, SOURCE_DESK, SOURCE_CONFIRMED,
                 key, status, vat))
            changed += cur.rowcount
        self._conn.commit()
        return changed

    def father_type_rows(self, *, config_dir: str | None = None) -> list[tuple]:
        """담당자가 정한 행 [(client_id, 차변계정코드, 세액, 유형 이름)] — 유형 기준표 맞춰 보기용(PII 없음).

        유형은 원본 유형 칸(원유형) — 담당자가 고른 것. 없으면(예전 행) 유형코드의 이름.
        """
        from kafa.config_loader import load_rules
        labels = {int(v): str(k) for k, v in
                  (load_rules(config_dir).get("vat_type_codes") or {}).items()}
        sql = ("SELECT client_id, 차변계정코드, 세액, 유형코드, 원유형 FROM vouchers WHERE skipped=0 "
               "AND 차변계정코드 IS NOT NULL AND 확정출처 IN (?,?)")
        out = []
        for r in self._conn.execute(sql, list(FATHER_SOURCES)):
            label = (r[4] or "").strip() or (labels.get(int(r[3]), "") if r[3] is not None else "")
            if label:
                out.append((r[0], int(r[1]), r[2], label))
        return out

    def source_counts(self) -> dict[str, int]:
        """확정출처별 건수(계정이 있는 행) — 담당자 판단이 얼마나 쌓였는지."""
        sql = ("SELECT COALESCE(확정출처,''), COUNT(*) FROM vouchers "
               "WHERE 차변계정코드 IS NOT NULL AND skipped=0 GROUP BY 1")
        return {k: int(n) for k, n in self._conn.execute(sql)}

    def board_rows(self) -> list[dict]:
        """고객별 집계(진행 현황 보드용). 값은 모두 비-PII 수치/기간."""
        sql = (
            "SELECT client_id, COUNT(*) AS vouchers, COUNT(DISTINCT period) AS periods, "
            "MAX(period) AS latest_period, MAX(ingested_at) AS last_ingested, "
            "COALESCE(SUM(공제여부='공제'),0) AS deduct, "
            "COALESCE(SUM(공제여부='불공제'),0) AS nondeduct, "
            "COALESCE(SUM(공제여부='검토'),0) AS review, "
            "COALESCE(SUM(판정유형='recommended'),0) AS recommended, "
            "COALESCE(SUM(판정유형='unresolved'),0) AS unresolved, "
            "COALESCE(SUM(skipped),0) AS skipped "
            "FROM vouchers GROUP BY client_id ORDER BY client_id"
        )
        cols = ["client_id", "vouchers", "periods", "latest_period", "last_ingested",
                "deduct", "nondeduct", "review", "recommended", "unresolved", "skipped"]
        return [dict(zip(cols, r)) for r in self._conn.execute(sql)]

    def record_run(self, run_id: str, inbox, files: int, written: int,
                   skipped: int, failures: int) -> None:
        self._conn.execute(
            "INSERT INTO runs(run_id,started_at,inbox,files,written,skipped,failures) "
            "VALUES(?,datetime('now'),?,?,?,?,?)",
            (run_id, str(inbox), files, written, skipped, failures))
        self._conn.commit()
