"""selector 보정 도우미 — 현재 화면의 후보 요소를 뽑아 준다.

selector 를 추측해 넣으면 엉뚱한 곳을 클릭할 수 있으므로, 실제 화면을 한 번 보고
config/fetch/wehago.yaml 을 채우는 절차를 둔다. 이 함수는 **화면 구조만** 읽고,
입력값·거래처명 같은 데이터는 출력하지 않는다(PII 유출 방지).

위하고 회계 화면은 아이콘 버튼(텍스트 없음)과 라벨이 떨어진 입력칸이 많아,
① 요소별 '라벨/대체텍스트'를 함께 뽑고 ② 엑셀·다운로드 관련 요소는 **숨은 것까지**
따로 정밀 스캔한다(메뉴가 접혀 있어도 찾을 수 있게).
"""
from __future__ import annotations

import re
import time

# 뽑아볼 요소 종류(역할별)
_PROBES = [
    ("입력창", "input:visible"),
    ("버튼", "button:visible"),
    ("링크", "a:visible"),
    ("선택", "select:visible"),
]

_KEYWORDS = ("엑셀", "다운로드", "조회", "검색", "거래처", "기간", "매입", "월")

# 정밀 스캔용(좁게) — 이 단어가 걸린 요소는 숨어 있어도 다 보여준다.
_DEEP_KEYWORDS = ("엑셀", "excel", "xls", "다운로드", "download", "내려받기",
                  "저장", "변환", "출력")

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
    // 같은 글자를 감싸기만 하는 바깥 요소는 건너뛴다(dl>dd>ul>li>a>span 사슬 제거).
    if (el.children.length === 1) {
      const only = el.children[0];
      const ot = (only.textContent || '').trim().replace(/\s+/g, ' ');
      if (ot && ot === (el.textContent || '').trim().replace(/\s+/g, ' ')) continue;
    }
    let cls = '';
    try {
      cls = (typeof el.className === 'string') ? el.className
          : (el.className && el.className.baseVal) || '';
    } catch (e) {}
    let sel = tag;
    if (el.id) sel += '#' + el.id;
    if (cls) sel += '.' + cls.trim().split(/\s+/).slice(0, 4).join('.');
    let vis = false, box = '';
    try {
      const r = el.getBoundingClientRect();
      vis = r.width > 0 && r.height > 0;
      box = Math.round(r.x) + ',' + Math.round(r.y) + ' ' +
            Math.round(r.width) + 'x' + Math.round(r.height);
    } catch (e) {}
    out.push({sel: sel.slice(0, 90), text: text, attrs: attrs.trim().slice(0, 160),
              vis: vis, box: box});
    if (out.length >= 200) break;
  }
  return out;
}
"""


# 화면 텍스트에 수임처 이름·사업자번호가 섞여 나오는 자리가 있다(예: 수임처 목록의 링크).
# 보안 제0원칙 — 원천 데이터는 어떤 LLM 컨텍스트에도 올리지 않는다. 구조만 남긴다.
_COMPANY_MARKERS = ("(주)", "㈜", "주식회사", "(유)", "유한회사", "(합)", "(재)", "(사)")
_BIZNO_RE = re.compile(r"\d{3}-?\d{2}-?\d{5}")
_LONGNUM_RE = re.compile(r"\d{8,}")


def _safe_text(text: str) -> str:
    """표시용 텍스트에서 회사명·사업자번호를 지운다(구조 파악에는 지장 없음)."""
    if not text:
        return text
    if any(m in text for m in _COMPANY_MARKERS):
        return "‹회사명›"
    text = _BIZNO_RE.sub("‹사업자번호›", text)
    return _LONGNUM_RE.sub("‹번호›", text)


def _info(el) -> dict:
    try:
        info = el.evaluate(_INFO_JS)
    except Exception:  # noqa: BLE001 — 화면 상태에 따라 실패해도 계속
        return {}
    return info if isinstance(info, dict) else {}


def _describe(el) -> tuple[str, str]:
    """(표시용 텍스트, 속성/라벨 문자열). 값(value)은 읽지 않는다."""
    try:
        text = _safe_text((el.inner_text() or "").strip().replace("\n", " ")[:30])
    except Exception:  # noqa: BLE001
        text = ""
    info = _info(el)
    attrs = info.get("attrs", "")
    extra = []
    for key, label in (("ctx", "라벨"), ("blind", "숨은텍스트"), ("alt", "아이콘")):
        v = _safe_text((info.get(key) or "").strip())
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
    # 보이는 것부터(누를 수 있는 후보가 먼저 보이게)
    hits = sorted(hits, key=lambda h: not h.get("vis"))
    for h in hits:
        mark = "보임" if h.get("vis") else "숨음"
        box = h.get("box") or ""
        out.append(f"   [{mark}] {h.get('sel', '')}" + (f"   ({box})" if box else ""))
        text = _safe_text((h.get("text") or "").strip())
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


pages_of = _pages          # 다른 모듈에서 쓸 수 있게 공개 이름


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


def _inspect_one(pg, pi: int, limit: int) -> tuple[list[str], bool]:
    """탭 하나(모든 프레임)의 요소 후보. (라인들, 무언가 찾았는지)."""
    out, found = [], False
    frames = _frames_of(pg)
    for fi, fr in enumerate(frames):
        where = f"탭#{pi}" if fi == 0 else f"탭#{pi}·iframe#{fi}: {_url_of(fr)}"
        lines = _inspect_frame(fr, where, limit)
        if lines:
            found = True
            out += lines
        out += _deep_scan(fr, where)
    return out, found


def _header() -> list[str]:
    return ["", "── 현재 화면의 후보 요소 (selector 보정용) ──",
            "※ 아래 id/name/class 를 config/fetch/wehago.yaml 에 적으세요.",
            "   예) period_from_input: \"#dateFrom\"  또는  \"input[name='fromDt']\"", ""]


def _inventory(pages) -> list[str]:
    out = [f"[화면 목록] 탭 {len(pages)}개"]
    for pi, pg in enumerate(pages):
        frames = _frames_of(pg)
        title = _title_of(pg)
        head = f"   탭#{pi} (프레임 {len(frames)}개) {_url_of(pg)}"
        out.append(head + (f"  제목={title!r}" if title else ""))
        for fi, fr in enumerate(frames[1:], start=1):
            out.append(f"      └ iframe#{fi} {_url_of(fr)}")
    out.append("")
    return out


def inspect_page(page, *, limit: int = 40) -> list[str]:
    """화면 요소 후보 목록(문자열 라인).

    회계 모듈이 **새 탭**으로 열리고 화면이 **iframe** 안에 있는 경우가 많아,
    같은 브라우저의 모든 탭 × 모든 프레임을 훑는다. 어디서 나온 요소인지 함께
    표시한다. 값(value)·거래처명 같은 데이터는 읽지 않는다.
    """
    pages = _pages(page)
    out = _header() + _inventory(pages)
    found = False
    for pi, pg in enumerate(pages):
        lines, hit = _inspect_one(pg, pi, limit)
        found = found or hit
        out += lines
    if not found:
        out.append("(요소를 찾지 못했습니다 — 회계 화면이 다른 탭/창에 있거나 아직 로딩 중일 수 "
                   "있습니다. 위 [화면 목록] 의 주소를 확인해 주세요.)")
    out.append("★ 표시는 '엑셀/다운로드/조회/거래처/기간' 같은 단어가 걸린 항목입니다.")
    out.append("")
    out += screen_hint(out)
    return out


# ── 감시 모드: 사람은 로그인·이동만, 화면 포착은 도구가 한다 ──

_SIG_JS = ("() => document.title + '|' + "
           "document.querySelectorAll('input,button,a,select').length")


def _signature(pg) -> str:
    """화면이 바뀌었는지 싸게 판별할 지문(제목 + 요소 수 + 주소)."""
    try:
        core = pg.evaluate(_SIG_JS)
    except Exception:  # noqa: BLE001
        core = ""
    return f"{_url_of(pg)}|{core}"


def is_ledger(lines: list[str]) -> bool:
    return "회계 전표 화면" in "\n".join(screen_hint(lines))


def is_dashboard(lines: list[str]) -> bool:
    return "대시보드" in "\n".join(screen_hint(lines))


def watch_screens(page, *, seconds: float = 300.0, interval: float = 2.0,
                  limit: int = 40, on_event=None, sleep=time.sleep,
                  now=time.monotonic) -> list[str]:
    """로그인 뒤 화면 전환을 지켜보다가 **회계 전표 화면이 뜨면 자동으로 잡는다**.

    사람은 로그인하고 평소처럼 메뉴를 눌러 이동하기만 하면 된다. 엔터 타이밍을
    맞출 필요가 없다. 지나친 화면 중 대시보드는 한 줄 요약만 남긴다(수임처 이름이
    잔뜩 있는 화면을 통째로 남기지 않기 위함 — 보안 제0원칙).
    """
    say = on_event or (lambda _m: None)
    deadline = now() + seconds
    seen: set[str] = set()
    log: list[str] = []
    captured: list[str] | None = None

    say(f"[감시] 최대 {int(seconds)}초 동안 화면을 지켜봅니다. "
        "평소처럼 수임처 › 회계 › 신용카드(매입) 로 이동하세요.")
    while captured is None and now() < deadline:
        for pi, pg in enumerate(_pages(page)):
            sig = _signature(pg)
            if not sig or sig in seen:
                continue
            seen.add(sig)
            lines, _ = _inspect_one(pg, pi, limit)
            where = f"탭#{pi} {_url_of(pg)} 제목={_title_of(pg)!r}"
            if is_ledger(lines):
                say(f"[감시] 회계 전표 화면을 찾았습니다 — {where}")
                captured = _header() + _inventory(_pages(page)) + lines
                break
            kind = "수임처 목록" if is_dashboard(lines) else "기타"
            say(f"[감시] 지나감({kind}) — {where}")
            log.append(f"   - {kind}: {where}")
        if captured is None:
            sleep(interval)

    if captured is None:
        out = _header() + _inventory(_pages(page))
        out.append("[감시] 제한 시간 안에 회계 전표 화면을 찾지 못했습니다.")
        out.append("[감시] 지나간 화면:")
        out += log or ["   (없음 — 화면이 바뀌지 않았습니다)"]
        out.append("")
        out += screen_hint(out)
        return out

    captured.append("★ 표시는 '엑셀/다운로드/조회/거래처/기간' 같은 단어가 걸린 항목입니다.")
    captured.append("")
    captured += screen_hint(captured)
    return captured
