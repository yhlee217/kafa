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


# ── 자동 넘기기: 사람이 페이지를 넘기지 않아도 되게 ──

_SCROLL_JS = r"""
(sel) => {
  // 목록이 들어 있는 **스크롤 가능한 상자**를 찾아 끝까지 내린다(창 스크롤이 아니라).
  let el = sel ? document.querySelector(sel) : null;
  if (!el) {
    const a = document.querySelector('a[id^="tooltip_"]');
    let cur = a && a.parentElement;
    while (cur) {
      const st = getComputedStyle(cur);
      if ((st.overflowY === 'auto' || st.overflowY === 'scroll') &&
          cur.scrollHeight > cur.clientHeight + 4) { el = cur; break; }
      cur = cur.parentElement;
    }
  }
  const target = el || document.scrollingElement || document.body;
  const before = target.scrollTop;
  target.scrollTop = target.scrollHeight;
  return target.scrollTop !== before;
}
"""

# 넘기기 방법을 못 찾았을 때, 쪽번호·'다음' 후보를 그대로 보여 준다(구조만).
_PAGER_JS = r"""
() => {
  const out = [];
  const words = ['다음', '더보기', '더 보기', '이전', 'next', 'more'];
  for (const el of document.querySelectorAll('button, a, li, span, div')) {
    if (el.children.length > 1) continue;
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 12) continue;
    const isNum = /^[0-9]{1,3}$/.test(t);
    const isWord = words.some(w => t.toLowerCase().includes(w));
    if (!isNum && !isWord) continue;
    let attrs = '';
    for (const a of el.attributes) {
      if (a.name === 'style') continue;
      attrs += ' ' + a.name + '="' + String(a.value).slice(0, 50) + '"';
    }
    let vis = false, box = '';
    try {
      const r = el.getBoundingClientRect();
      vis = r.width > 0 && r.height > 0;
      box = Math.round(r.x) + ',' + Math.round(r.y);
    } catch (e) {}
    out.push({tag: el.tagName.toLowerCase(), text: t,
              attrs: attrs.trim().slice(0, 140), vis: vis, box: box});
    if (out.length >= 60) break;
  }
  return out;
}
"""


def pagination_report(page) -> list[str]:
    """쪽번호·'다음' 후보를 뽑아 둔다 — 자동 넘기기가 안 될 때 보정용."""
    out = ["", "── 목록 넘기기 후보 (discover 보정용) ──", ""]
    found = False
    for i, pg in enumerate(pages_of(page)):
        try:
            hits = pg.evaluate(_PAGER_JS)
        except Exception:  # noqa: BLE001
            continue
        if not hits:
            continue
        found = True
        out.append(f"[탭#{i}] 후보 {len(hits)}개")
        for h in hits:
            mark = "보임" if h.get("vis") else "숨음"
            out.append(f"   [{mark}] <{h.get('tag')}> {h.get('text')!r}  "
                       f"{h.get('attrs')}  ({h.get('box')})")
        out.append("")
    if not found:
        out.append("(후보를 찾지 못했습니다 — 목록 화면이 맞나요?)")
    return out


def _try_click(pg, selector: str, timeout_ms: int) -> bool:
    try:
        pg.click(selector, timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 — 없는 버튼이면 다음 방법으로
        return False


def _scroll(pg, container) -> bool:
    try:
        return bool(pg.evaluate(_SCROLL_JS, container))
    except Exception:  # noqa: BLE001
        return False


def _do_strategy(page, cfg: dict, kind: str, next_no: int) -> str:
    """한 가지 방법으로 다음 페이지를 시도. 성공하면 설명, 실패하면 빈 문자열.

    '눌렀다'가 곧 '넘어갔다'는 아니다 — 실제로 새 항목이 나왔는지는 부르는 쪽에서
    확인하고, 안 늘면 다음 방법으로 넘어간다.
    """
    from kafa.fetch.wehago import _as_list

    d = (cfg or {}).get("discover", {}) or {}
    t = int(d.get("click_timeout_ms", 2500))
    for pg in pages_of(page):
        if kind == "number":
            for tmpl in _as_list(d.get("page_number_button")):
                if _try_click(pg, tmpl.replace("{n}", str(next_no)), t):
                    return f"쪽번호 {next_no}"
        elif kind == "next":
            for cand in _as_list(d.get("next_buttons")):
                if _try_click(pg, cand, t):
                    return "다음 버튼"
        elif kind == "scroll" and d.get("scroll", True):
            if _scroll(pg, d.get("scroll_container")):
                return "스크롤"
    return ""


_STRATEGIES = ("number", "next", "scroll")


def auto_collect(page, cfg: dict, *, on_event=None, sleep=None,
                 known: dict | None = None) -> dict:
    """페이지를 스스로 넘기며 전체 수임처를 모은다.

    한 방법이 항목을 늘리지 못하면 **다음 방법으로 바꾼다**(쪽번호 → '다음' → 스크롤).
    이름은 돌려주는 dict 에만 담기고 화면에는 건수만 출력한다(보안 제0원칙).
    """
    import time as _time

    say = on_event or (lambda _m: None)
    sleep = sleep or _time.sleep
    d = (cfg or {}).get("discover", {}) or {}
    max_pages = int(d.get("max_pages", 40))
    wait = float(d.get("wait_seconds", 1.5))
    known = known if known is not None else {}

    total = expected_total(page)
    if total:
        say(f"화면 표시: 담당 수임처 {total}곳")
    si, next_no, first = 0, 2, True
    for _ in range(max_pages):
        added = merge(known, collect_clients(page))
        left = f" / 남은 것 약 {max(0, total - len(known))}곳" if total else ""
        say(f"모은 수임처 {len(known)}곳 (+{added}){left}")
        if total and len(known) >= total:
            say("전부 모았습니다.")
            return known
        if added == 0 and not first:
            si += 1                      # 눌렸어도 안 늘었다 → 다른 방법으로
            if si < len(_STRATEGIES):
                say(f"방법을 바꿉니다 → {_STRATEGIES[si]}")
        first = False
        # 실행되는 방법을 찾을 때까지 넘어간다(없는 버튼이면 바로 다음 방법).
        how = ""
        while si < len(_STRATEGIES) and not how:
            how = _do_strategy(page, cfg, _STRATEGIES[si], next_no)
            if not how:
                si += 1
        if not how:
            say("넘기는 방법을 못 찾아 멈춥니다.")
            return known
        if how.startswith("쪽번호"):
            next_no += 1
        say(f"다음 페이지로 ({how})")
        sleep(wait)
    say(f"최대 {max_pages}쪽까지만 봅니다.")
    return known
