"""selector 보정 도우미 — 현재 화면의 후보 요소를 뽑아 준다.

selector 를 추측해 넣으면 엉뚱한 곳을 클릭할 수 있으므로, 실제 화면을 한 번 보고
config/fetch/wehago.yaml 을 채우는 절차를 둔다. 이 함수는 **화면 구조만** 읽고,
입력값·거래처명 같은 데이터는 출력하지 않는다(PII 유출 방지).

위하고 회계 화면은 아이콘 버튼(텍스트 없음)과 라벨이 떨어진 입력칸이 많아,
① 요소별 '라벨/대체텍스트'를 함께 뽑고 ② 엑셀·다운로드 관련 요소는 **숨은 것까지**
따로 정밀 스캔한다(메뉴가 접혀 있어도 찾을 수 있게).
"""
from __future__ import annotations

# 뽑아볼 요소 종류(역할별)
_PROBES = [
    ("입력창", "input:visible"),
    ("버튼", "button:visible"),
    ("링크", "a:visible"),
    ("선택", "select:visible"),
]

_KEYWORDS = ("엑셀", "다운로드", "조회", "검색", "거래처", "기간", "매입", "월")

# 정밀 스캔용(좁게) — 이 단어가 걸린 요소는 숨어 있어도 다 보여준다.
_DEEP_KEYWORDS = ("엑셀", "excel", "xls", "다운로드", "download", "내려받기", "저장")

# 요소 하나의 라벨/대체텍스트/속성 — 값(value)은 읽지 않는다.
_INFO_JS = r"""
(el) => {
  const attrs = [];
  for (const a of el.attributes) {
    if (a.name === 'value' || a.name === 'style') continue;   // 입력값·인라인스타일 제외
    attrs.push(a.name + '="' + String(a.value).slice(0, 60) + '"');
  }
  let ctx = '';
  try { if (el.labels && el.labels.length) ctx = el.labels[0].textContent; } catch (e) {}
  if (!ctx && el.id) {
    try {
      const esc = (window.CSS && CSS.escape) ? CSS.escape(el.id) : el.id;
      const f = document.querySelector('label[for="' + esc + '"]');
      if (f) ctx = f.textContent;
    } catch (e) {}
  }
  if (!ctx) {
    let p = el.parentElement, d = 0;
    while (p && d < 3) {
      const t = (p.innerText || '').trim();
      if (t) { ctx = t; break; }
      p = p.parentElement; d++;
    }
  }
  const b = el.querySelector('.blind, .sr-only, .ir, .hide, .hidden-text');
  const img = el.querySelector('img[alt], svg title');
  return {
    attrs: attrs.join(' ').slice(0, 220),
    ctx: (ctx || '').replace(/\s+/g, ' ').slice(0, 40),
    blind: b ? (b.textContent || '').trim().slice(0, 30) : '',
    alt: img ? String(img.getAttribute ? (img.getAttribute('alt') || '')
                                       : (img.textContent || '')).slice(0, 30) : ''
  };
}
"""

# 문서 전체에서 키워드가 걸린 요소(숨은 것 포함) — 접힌 메뉴 안의 버튼을 찾기 위함.
_SCAN_JS = r"""
(kw) => {
  const skip = new Set(['script','style','meta','link','head','html','body','title']);
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const tag = el.tagName.toLowerCase();
    if (skip.has(tag)) continue;
    if (el.children.length > 2) continue;          // 큰 컨테이너는 건너뜀
    let attrs = '';
    for (const a of el.attributes) {
      if (a.name === 'value' || a.name === 'style') continue;
      attrs += ' ' + a.name + '="' + String(a.value).slice(0, 60) + '"';
    }
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    const hay = (text + attrs).toLowerCase();
    if (!kw.some(k => hay.includes(k))) continue;
    let cls = '';
    try {
      cls = (typeof el.className === 'string') ? el.className
          : (el.className && el.className.baseVal) || '';
    } catch (e) {}
    let sel = tag;
    if (el.id) sel += '#' + el.id;
    if (cls) sel += '.' + cls.trim().split(/\s+/).slice(0, 4).join('.');
    let vis = false;
    try { const r = el.getBoundingClientRect(); vis = r.width > 0 && r.height > 0; } catch (e) {}
    out.push({sel: sel.slice(0, 90), text: text, attrs: attrs.trim().slice(0, 160), vis: vis});
    if (out.length >= 60) break;
  }
  return out;
}
"""


def _info(el) -> dict:
    try:
        info = el.evaluate(_INFO_JS)
    except Exception:  # noqa: BLE001 — 화면 상태에 따라 실패해도 계속
        return {}
    return info if isinstance(info, dict) else {}


def _describe(el) -> tuple[str, str]:
    """(표시용 텍스트, 속성/라벨 문자열). 값(value)은 읽지 않는다."""
    try:
        text = (el.inner_text() or "").strip().replace("\n", " ")[:30]
    except Exception:  # noqa: BLE001
        text = ""
    info = _info(el)
    attrs = info.get("attrs", "")
    extra = []
    for key, label in (("ctx", "라벨"), ("blind", "숨은텍스트"), ("alt", "아이콘")):
        v = (info.get(key) or "").strip()
        if v and v != text:
            extra.append(f"{label}={v!r}")
    if extra:
        attrs = " ".join(extra) + "  |  " + attrs
    return text, attrs


def _inspect_frame(frame, label: str, limit: int) -> list[str]:
    """한 프레임의 요소 후보. 값(value)은 읽지 않는다."""
    out: list[str] = []
    for kind, css in _PROBES:
        try:
            els = frame.query_selector_all(css)
        except Exception:  # noqa: BLE001
            continue
        if not els:
            continue
        rows = []
        for el in els:
            text, attrs = _describe(el)
            if not attrs and not text:
                continue
            hit = any(k in text or k in attrs for k in _KEYWORDS)
            rows.append((hit, f"   {'★ ' if hit else '  '}{text!r:<24} {attrs}"))
        if not rows:
            continue
        rows.sort(key=lambda r: not r[0])          # 키워드 매칭 우선 노출
        out.append(f"[{kind}] {len(els)}개  ({label})")
        out += [r[1] for r in rows[:limit]]
        if len(rows) > limit:
            out.append(f"   … 외 {len(rows) - limit}개")
        out.append("")
    return out


def _deep_scan(frame, label: str) -> list[str]:
    """엑셀/다운로드 관련 요소를 숨은 것까지 훑는다(접힌 메뉴 대비)."""
    try:
        hits = frame.evaluate(_SCAN_JS, list(_DEEP_KEYWORDS))
    except Exception:  # noqa: BLE001
        return []
    if not hits:
        return []
    out = [f"[엑셀·다운로드 정밀 스캔] {len(hits)}개  ({label}) — 숨은 요소 포함"]
    for h in hits:
        mark = "보임" if h.get("vis") else "숨음"
        out.append(f"   [{mark}] {h.get('sel', '')}")
        text = (h.get("text") or "").strip()
        if text:
            out.append(f"           텍스트={text!r}")
        attrs = (h.get("attrs") or "").strip()
        if attrs:
            out.append(f"           {attrs}")
    out.append("")
    return out


def _pages(page) -> list:
    """같은 브라우저의 **모든 탭**. 회계 모듈이 새 탭/새 창으로 열리는 경우 대비."""
    try:
        pages = [pg for pg in page.context.pages if not pg.is_closed()]
    except Exception:  # noqa: BLE001 — context 가 없는 구현(테스트 더블 등)
        pages = []
    try:
        closed = bool(page.is_closed())
    except Exception:  # noqa: BLE001
        closed = False
    if not closed and page not in pages:
        pages.insert(0, page)
    return pages or [page]


def _frames_of(page) -> list:
    try:
        return list(page.frames)
    except Exception:  # noqa: BLE001
        return [page]


def _url_of(obj) -> str:
    try:
        return (obj.url or "")[:70]
    except Exception:  # noqa: BLE001
        return ""


def _title_of(page) -> str:
    """탭 제목. 주소가 about:blank 로만 보이는 화면이 있어 구분에 쓴다."""
    try:
        return (page.title() or "").strip()[:40]
    except Exception:  # noqa: BLE001
        return ""


# 어느 화면을 잡았는지 사람에게 바로 알려주기 위한 표식(구조만 — 데이터 아님).
_DASHBOARD_MARKERS = ("회사명, 사업자등록번호, 대표자명으로 검색",
                      "수집정보등록", "새 수임처")
_LEDGER_MARKERS = ("상세검색", "휴폐업조회", "nm_bizcond", "전표상태", "전표전송")


def screen_hint(lines: list[str]) -> list[str]:
    """잡은 화면이 맞는지 한 줄 판정 — 잘못 잡았으면 무엇을 할지 알려준다."""
    text = "\n".join(lines)
    if any(m in text for m in _DASHBOARD_MARKERS):
        return ["[판정] 지금 잡힌 화면은 **수임처 목록(대시보드)** 입니다.",
                "       수임처를 하나 선택해 회계 › 전표관리 › 신용카드(매입) 화면까지",
                "       이동한 뒤, 터미널에서 r + 엔터로 다시 살펴보세요."]
    hits = sum(1 for m in _LEDGER_MARKERS if m in text)
    if hits >= 2:
        return ["[판정] 회계 전표 화면으로 보입니다. 이 결과를 보내주세요."]
    return ["[판정] 어떤 화면인지 확신할 수 없습니다. 신용카드(매입) 목록과",
            "       조회 기간·엑셀 버튼이 다 보이는 상태에서 r + 엔터로 다시 해보세요."]


def inspect_page(page, *, limit: int = 40) -> list[str]:
    """화면 요소 후보 목록(문자열 라인).

    회계 모듈이 **새 탭**으로 열리고 화면이 **iframe** 안에 있는 경우가 많아,
    같은 브라우저의 모든 탭 × 모든 프레임을 훑는다. 어디서 나온 요소인지 함께
    표시한다. 값(value)·거래처명 같은 데이터는 읽지 않는다.
    """
    out = ["", "── 현재 화면의 후보 요소 (selector 보정용) ──",
           "※ 아래 id/name/class 를 config/fetch/wehago.yaml 에 적으세요.",
           "   예) period_from_input: \"#dateFrom\"  또는  \"input[name='fromDt']\"", ""]

    pages = _pages(page)
    inventory = []
    for pi, pg in enumerate(pages):
        frames = _frames_of(pg)
        inventory.append((pi, pg, frames))
    out.append(f"[화면 목록] 탭 {len(pages)}개")
    for pi, pg, frames in inventory:
        title = _title_of(pg)
        head = f"   탭#{pi} (프레임 {len(frames)}개) {_url_of(pg)}"
        out.append(head + (f"  제목={title!r}" if title else ""))
        for fi, fr in enumerate(frames[1:], start=1):
            out.append(f"      └ iframe#{fi} {_url_of(fr)}")
    out.append("")

    found = False
    for pi, pg, frames in inventory:
        for fi, fr in enumerate(frames):
            where = f"탭#{pi}" if fi == 0 else f"탭#{pi}·iframe#{fi}: {_url_of(fr)}"
            lines = _inspect_frame(fr, where, limit)
            if lines:
                found = True
                out += lines
            out += _deep_scan(fr, where)
    if not found:
        out.append("(요소를 찾지 못했습니다 — 회계 화면이 다른 탭/창에 있거나 아직 로딩 중일 수 "
                   "있습니다. 위 [화면 목록] 의 주소를 확인해 주세요.)")
    out.append("★ 표시는 '엑셀/다운로드/조회/거래처/기간' 같은 단어가 걸린 항목입니다.")
    out.append("")
    out += screen_hint(out)
    return out
