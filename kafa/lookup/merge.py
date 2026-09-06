"""받아온 이용내역을 대행사 건에 붙인다 — 전부 로컬 코드가 한다.

대조 키는 `수임처 + 날짜 + 금액`. 승인번호는 위하고 자료에 없으므로 키가 아니라
**결과로 얻는 값**이다. 실측 유일성 99.7%(1,797/1,803, 충돌 3건).

붙였다고 끝이 아니다. 카드사 내역에도 결제대행사 이름만 있는 경우가 있어서,
찾은 이름이 **또 대행사면** 그렇게 표시한다 — 그게 이 소스가 소용없다는 증거다.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from kafa.lookup.statements import (StatementRow, normalize_amount,
                                    normalize_date, read_statement)
from kafa.rules.agents import agent_of

# 대조 결과
FOUND = "찾음"              # 실제 가맹점을 얻었다
STILL_AGENT = "여전히대행사"  # 붙었지만 카드사 내역에도 대행사 이름뿐
AMBIGUOUS = "중복"          # 같은 날 같은 금액이 여러 건 — 사람이 골라야 한다
MISSING = "못찾음"           # 이용내역에 해당 건이 없다


@dataclass
class Resolved:
    """대행사 건 한 줄의 대조 결과."""
    번호: str = ""
    client_id: str = ""
    수임처: str = ""
    거래일자: str = ""
    합계: Decimal = Decimal(0)
    대행사: str = ""
    갈래: str = ""
    결과: str = MISSING
    가맹점: str = ""
    승인번호: str = ""
    출처: str = ""

    @property
    def 해소됨(self) -> bool:
        return self.결과 == FOUND


@dataclass
class MergeReport:
    """이 소스가 쓸 만한지 알려주는 숫자 — 다음 달 판단의 근거."""
    대상: int = 0
    찾음: int = 0
    여전히대행사: int = 0
    중복: int = 0
    못찾음: int = 0
    이용내역_줄수: int = 0
    갈래별_찾음: dict[str, int] = field(default_factory=dict)

    @property
    def 해소율(self) -> float:
        return (self.찾음 / self.대상 * 100) if self.대상 else 0.0

    def 요약(self) -> str:
        return (f"대상 {self.대상}건 / 이용내역 {self.이용내역_줄수}줄 → "
                f"찾음 {self.찾음}건({self.해소율:.1f}%), "
                f"여전히 대행사 {self.여전히대행사}건, 중복 {self.중복}건, "
                f"못찾음 {self.못찾음}건")


def _read_targets(path: str | Path) -> list[Resolved]:
    out: list[Resolved] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(Resolved(
                번호=row.get("번호", ""), client_id=row.get("client_id", ""),
                수임처=row.get("수임처", ""),
                거래일자=normalize_date(row.get("거래일자", "")),
                합계=normalize_amount(row.get("합계", "")),
                대행사=row.get("거래처", ""), 갈래=row.get("갈래", "")))
    return out


def _index(rows: list[StatementRow]) -> dict[tuple[str, Decimal], list[StatementRow]]:
    """(날짜, 금액) → 이용내역 줄들. 취소 건은 부호가 반대라 절댓값으로도 찾는다."""
    out: dict[tuple[str, Decimal], list[StatementRow]] = defaultdict(list)
    for row in rows:
        out[(row.날짜, row.금액)].append(row)
        if row.금액 < 0:
            out[(row.날짜, -row.금액)].append(row)
    return out


def merge_statements(targets_csv: str | Path, statement_paths, out_csv: str | Path,
                     *, config_dir: str | None = None) -> tuple[list[Resolved], MergeReport]:
    """의뢰 목록 + 받아온 이용내역 → 대조 결과 CSV.

    이용내역 파일이 어느 수임처 것인지는 파일 이름의 번호로 가른다
    (`012_삼성카드_2026-01.csv` → 12번). 번호를 못 읽으면 전체에서 찾는다.
    """
    targets = _read_targets(targets_csv)
    report = MergeReport(대상=len(targets))

    per_number: dict[str, list[StatementRow]] = defaultdict(list)
    everything: list[StatementRow] = []
    for path in statement_paths:
        file = Path(path)
        rows = read_statement(file, config_dir=config_dir)
        everything.extend(rows)
        head = file.stem.split("_", 1)[0].lstrip("0") or "0"
        if head.isdigit():
            per_number[head].extend(rows)
    report.이용내역_줄수 = len(everything)

    indexes = {n: _index(rows) for n, rows in per_number.items()}
    fallback = _index(everything)

    for t in targets:
        key = (t.거래일자, t.합계)
        number = (t.번호 or "").lstrip("0") or "0"
        hits = indexes.get(number, {}).get(key) if number in indexes else None
        if hits is None:
            hits = fallback.get(key, [])
        if not hits:
            report.못찾음 += 1
            continue
        if len({(h.가맹점, h.승인번호) for h in hits}) > 1:
            t.결과 = AMBIGUOUS
            t.가맹점 = " | ".join(sorted({h.가맹점 for h in hits if h.가맹점})[:3])
            t.출처 = hits[0].출처
            report.중복 += 1
            continue
        hit = hits[0]
        t.가맹점, t.승인번호, t.출처 = hit.가맹점, hit.승인번호, hit.출처
        if agent_of(hit.가맹점 or "", config_dir=config_dir):
            t.결과 = STILL_AGENT
            report.여전히대행사 += 1
        else:
            t.결과 = FOUND
            report.찾음 += 1
            report.갈래별_찾음[t.갈래] = report.갈래별_찾음.get(t.갈래, 0) + 1

    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "수임처", "거래일자", "합계", "대행사", "갈래",
                    "결과", "찾은가맹점", "승인번호", "출처"])
        for t in targets:
            w.writerow([t.번호, t.수임처, t.거래일자, t.합계, t.대행사, t.갈래,
                        t.결과, t.가맹점, t.승인번호, t.출처])
    return targets, report
