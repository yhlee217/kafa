"""동작 기록 — 사람이 한 번 직접 받아보면, 그 순서를 그대로 적어 둔다.

화면만 훑어서는 알 수 없는 것들이 있다(어느 버튼이 엑셀 메뉴를 여는지, 달력을 어떻게
고르는지). 그래서 **사람이 평소처럼 한 번 다운로드**하는 동안 클릭·입력 순서를 기록해
selector 보정에 쓴다. 자동 로그인·비밀번호는 여전히 다루지 않는다.

기록하는 것은 **어느 요소를 눌렀는가**(태그/id/class 경로)뿐이다. 입력값은 날짜·숫자 외에는
마스킹하고(거래처 검색어에 실명이 들어갈 수 있다), 회사명·사업자번호는 지운다.
"""
from __future__ import annotations

import re
import time

from kafa.fetch.inspect import _frames_of, _pages, _safe_text, _title_of, _url_of

# 페이지 안에 심는 기록기. 클릭/입력을 window.__kafa_rec 에 쌓아 두고, 파이썬이 주기적으로
# 비워 간다(값은 여기서 그대로 두고, 마스킹은 파이썬에서 한다).
_RECORDER_JS = r"""
() => {
  if (window.__kafa_rec) return 'already';
  window.__kafa_rec = [];
  const one = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) return s + '#' + el.id;
    let cls = '';
    try {
      cls = (typeof el.className === 'string') ? el.className
          : (el.className && el.className.baseVal) || '';
    } catch (e) {}
    cls = cls.trim();
    if (cls) s += '.' + cls.split(/\s+/).slice(0, 3).join('.');
    return s;
  };
  const path = (el) => {
    const parts = [];
    let cur = el, d = 0;
    while (cur && cur.nodeType === 1 && d < 4) {
      parts.unshift(one(cur));
      if (cur.id) break;
      cur = cur.parentElement; d++;
    }
    return parts.join(' > ');
  };
  const push = (type, el) => {
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'html' || tag === 'body') return;
    let attrs = '';
    try {
      for (const a of el.attributes) {
        if (a.name === 'style' || a.name === 'value') continue;
        attrs += ' ' + a.name + '="' + String(a.value).slice(0, 50) + '"';
      }
    } catch (e) {}
    window.__kafa_rec.push({
      type: type,
      path: path(el).slice(0, 160),
      tag: tag,
      text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40),
      attrs: attrs.trim().slice(0, 160),
      value: (type === 'change' && el.value != null) ? String(el.value).slice(0, 40) : ''
    });
  };
  document.addEventListener('click', (e) => push('click', e.target), true);
  document.addEventListener('change', (e) => push('change', e.target), true);
  return 'installed';
}
"""

_DRAIN_JS = "() => { const a = window.__kafa_rec || []; window.__kafa_rec = []; return a; }"

# 날짜·기간처럼 그대로 남겨도 되는 값만 통과시킨다(그 외 입력값은 실명일 수 있다).
_SAFE_VALUE_RE = re.compile(r"^[\d.\-/ :~]{1,30}$")


def _mask_value(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    return v if _SAFE_VALUE_RE.match(v) else "‹입력값›"


def install(frame) -> bool:
    """프레임에 기록기를 심는다(이미 있으면 그대로). 실패해도 조용히 넘어간다."""
    try:
        return frame.evaluate(_RECORDER_JS) in ("installed", "already")
    except Exception:  # noqa: BLE001 — 로딩 중·교차출처 프레임 등
        return False


def drain(frame) -> list[dict]:
    """쌓인 기록을 가져오고 비운다."""
    try:
        events = frame.evaluate(_DRAIN_JS)
    except Exception:  # noqa: BLE001
        return []
    return [e for e in events if isinstance(e, dict)]


def format_steps(events: list[dict], *, downloads: list[str] = None) -> list[str]:
    """기록을 사람이 읽고, 그대로 config 에 옮길 수 있는 형태로."""
    out = ["", "── 기록된 동작 (config/fetch/wehago.yaml 보정용) ──", ""]
    if not events:
        out.append("(기록된 클릭이 없습니다 — 브라우저에서 실제로 조작하셨는지 확인해 주세요)")
    for i, e in enumerate(events, start=1):
        kind = "클릭" if e.get("type") == "click" else "입력"
        text = _safe_text(e.get("text") or "")
        out.append(f"{i:2d}. [{kind}] {e.get('path', '')}")
        if text:
            out.append(f"      텍스트={text!r}")
        attrs = (e.get("attrs") or "").strip()
        if attrs:
            out.append(f"      {attrs}")
        val = _mask_value(e.get("value", ""))
        if val:
            out.append(f"      값={val}")
        where = e.get("where")
        if where:
            out.append(f"      위치={where}")
        if e.get("download"):
            out.append("      ↑↑↑ 이 동작 직후 **파일이 내려받아졌습니다**")
    out.append("")
    if downloads:
        out.append(f"[다운로드] {len(downloads)}건 감지: " + ", ".join(downloads[:5]))
    else:
        out.append("[다운로드] 감지되지 않았습니다 — 실제로 엑셀을 받아보셨나요?")
    return out


def record_flow(page, *, seconds: float = 300.0, interval: float = 1.0,
                stop_after_download: bool = True, on_event=None,
                sleep=time.sleep, now=time.monotonic) -> list[str]:
    """사람이 한 번 직접 받는 동안 클릭·입력 순서를 기록한다.

    다운로드가 감지되면(기본) 잠깐 더 지켜본 뒤 끝낸다. 자동 로그인은 하지 않는다.
    """
    say = on_event or (lambda _m: None)
    deadline = now() + seconds
    events: list[dict] = []
    downloads: list[str] = []
    hooked: set[int] = set()
    grace_until: float | None = None

    say(f"[기록] 최대 {int(seconds)}초 동안 조작을 기록합니다. "
        "브라우저에서 평소처럼 기간을 고르고 조회한 뒤 엑셀을 한 번 받아 보세요.")

    while now() < deadline:
        for pi, pg in enumerate(_pages(page)):
            key = id(pg)
            if key not in hooked:
                hooked.add(key)
                try:
                    pg.on("download", lambda d: downloads.append(
                        getattr(d, "suggested_filename", "") or "download"))
                except Exception:  # noqa: BLE001 — 이벤트를 못 붙여도 기록은 계속
                    pass
            for fi, fr in enumerate(_frames_of(pg)):
                install(fr)
                where = f"탭#{pi}" if fi == 0 else f"탭#{pi}·iframe#{fi}"
                for e in drain(fr):
                    e["where"] = where
                    events.append(e)
                    say(f"[기록] {len(events):2d}. {e.get('type')} "
                        f"{e.get('path', '')[:70]} {_safe_text(e.get('text') or '')}")
        if downloads and events and not events[-1].get("download"):
            events[-1]["download"] = True
            say(f"[기록] 파일 내려받기 감지 — 직전 동작을 표시했습니다.")
        if stop_after_download and downloads:
            if grace_until is None:
                grace_until = now() + 3.0
            elif now() >= grace_until:
                break
        sleep(interval)

    head = [f"[화면] {_title_of(pg)!r} {_url_of(pg)}"
            for pg in _pages(page)]
    return head + format_steps(events, downloads=downloads)
