"""수임처 목록·접속 URL 을 화면에서 직접 모은다 — 별도 엑셀 없이.

대시보드의 수임처 링크는 `id="tooltip_<수임처코드>"` 이고, 이 코드가 신용카드 화면
주소의 `cno` 와 같다. 그래서 ① 아무 수임처의 신용카드 화면을 한 번 열어 **주소 틀**을
배우고 ② 목록 화면에서 코드·이름을 모으면, 로그인만 하면 전체를 주소로 순회할 수 있다.

모은 이름은 **로컬 파일에만** 쓴다(보안 제0원칙 — 어떤 LLM 컨텍스트에도 올리지 않는다).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from kafa.fetch.inspect import pages_of

# 목록 화면에서 (수임처코드, 이름) 을 뽑는다. 값·툴팁 본문은 읽지 않는다.
_COLLECT_JS = r"""
() => {
  const out = [];
  for (const a of document.querySelectorAll('a[id^="tooltip_"]')) {
    const cno = a.id.replace('tooltip_', '');
    const name = (a.textContent || '').trim().replace(/\s+/g, ' ');
    if (/^[0-9]+$/.test(cno) && name) out.push({cno: cno, name: name});
  }
  return out;
}
"""

_CNO_RE = re.compile(r"(cno=)\d+")

# 화면에 '담당 수임처136' / '수임처 136개' 처럼 전체 수가 적혀 있다 — 다 모았는지 확인용.
_TOTAL_JS = ("() => (document.body && document.body.innerText || '')"
             r".replace(/\s+/g, ' ').slice(0, 4000)")
_TOTAL_RE = re.compile(r"수임처\s*(\d{1,4})\s*개|담당\s*수임처\s*(\d{1,4})")


def expected_total(page) -> int:
    """화면이 알려주는 담당 수임처 수. 못 찾으면 0."""
    for pg in pages_of(page):
        try:
            text = pg.evaluate(_TOTAL_JS) or ""
        except Exception:  # noqa: BLE001
            continue
        m = _TOTAL_RE.search(text)
        if m:
            return int(m.group(1) or m.group(2))
    return 0


def url_template(url: str) -> str:
    """'...?sao&cno=10049328&x=1' → '...?sao&cno={cno}&x=1'. cno 가 없으면 빈 문자열."""
    url = (url or "").strip()
    if not _CNO_RE.search(url):
        return ""
    return _CNO_RE.sub(r"\1{cno}", url)


def find_url_template(page) -> str:
    """열려 있는 탭 중 cno 가 들어 있는 주소에서 틀을 만든다."""
    for pg in pages_of(page):
        try:
            tmpl = url_template(pg.url or "")
        except Exception:  # noqa: BLE001
            continue
        if tmpl:
            return tmpl
    return ""


def collect_clients(page) -> list[dict]:
    """지금 보이는 화면들에서 (코드, 이름) 을 모은다(모든 탭)."""
    rows: list[dict] = []
    for pg in pages_of(page):
        try:
            found = pg.evaluate(_COLLECT_JS)
        except Exception:  # noqa: BLE001 — 목록이 없는 탭은 건너뜀
            continue
        for r in found or []:
            if isinstance(r, dict) and r.get("cno") and r.get("name"):
                rows.append({"cno": str(r["cno"]), "name": str(r["name"])})
    return rows


def merge(known: dict, rows: list[dict]) -> int:
    """이미 모은 것에 합치고, 새로 늘어난 수를 돌려준다."""
    before = len(known)
    for r in rows:
        known.setdefault(r["cno"], r["name"])
    return len(known) - before


def write_csv(path, known: dict, template: str = "") -> Path:
    """--master 로 바로 쓸 수 있는 목록 파일(로컬 전용 — 수임처 실명이 들어 있다).

    접속 URL 은 만들지 않는다: 화면 주소에 수임처마다 다른 값(cd_com·gisu·companyID·
    taxNum)이 들어 있어 코드만 바꿔 쓸 수 없다. 대신 **수임처코드**를 저장해 두고,
    수집할 때 목록에서 a#tooltip_<코드> 를 눌러 연다.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        header = ["회사명", "수임처코드"] + (["접속 URL"] if template else [])
        w.writerow(header)
        for cno, name in sorted(known.items(), key=lambda kv: kv[1]):
            row = [name, cno]
            if template:
                row.append(template.replace("{cno}", cno))
            w.writerow(row)
    return out
