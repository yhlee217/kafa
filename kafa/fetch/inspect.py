"""selector 보정 도우미 — 현재 화면의 후보 요소를 뽑아 준다.

selector 를 추측해 넣으면 엉뚱한 곳을 클릭할 수 있으므로, 실제 화면을 한 번 보고
config/fetch/wehago.yaml 을 채우는 절차를 둔다. 이 함수는 **화면 구조만** 읽고,
입력값·거래처명 같은 데이터는 출력하지 않는다(PII 유출 방지).
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


def _attrs(el) -> str:
    parts = []
    for name in ("id", "name", "class", "type", "placeholder", "title", "aria-label"):
        v = el.get_attribute(name)
        if v:
            v = v.strip()
            if len(v) > 60:
                v = v[:57] + "…"
            parts.append(f'{name}="{v}"')
    return " ".join(parts)


def _inspect_frame(frame, label: str, limit: int) -> list[str]:
    """한 프레임의 요소 후보. 값(value)은 읽지 않는다."""
    out: list[str] = []
    for kind, css in _PROBES:
        try:
            els = frame.query_selector_all(css)
        except Exception:  # noqa: BLE001 — 화면 상태에 따라 실패해도 계속
            continue
        if not els:
            continue
        rows = []
        for el in els:
            try:
                text = (el.inner_text() or "").strip().replace("\n", " ")[:30]
                a = _attrs(el)
            except Exception:  # noqa: BLE001
                continue
            if not a and not text:
                continue
            hit = any(k in text or k in a for k in _KEYWORDS)
            rows.append((hit, f"   {'★ ' if hit else '  '}{text!r:<32} {a}"))
        if not rows:
            continue
        rows.sort(key=lambda r: not r[0])          # 키워드 매칭 우선 노출
        out.append(f"[{kind}] {len(els)}개  ({label})")
        out += [r[1] for r in rows[:limit]]
        if len(rows) > limit:
            out.append(f"   … 외 {len(rows) - limit}개")
        out.append("")
    return out


def inspect_page(page, *, limit: int = 40) -> list[str]:
    """화면 요소 후보 목록(문자열 라인).

    회계 화면이 iframe 안에 있는 경우가 많아 **모든 프레임**을 훑고, 어느 프레임에서
    나온 요소인지 함께 표시한다. 값(value)·거래처명 같은 데이터는 읽지 않는다.
    """
    out = ["", "── 현재 화면의 후보 요소 (selector 보정용) ──",
           "※ 아래 id/name/class 를 config/fetch/wehago.yaml 에 적으세요.",
           "   예) period_from_input: \"#dateFrom\"  또는  \"input[name='fromDt']\"", ""]

    try:
        frames = list(page.frames)
    except Exception:  # noqa: BLE001
        frames = [page]
    if len(frames) > 1:
        out.append(f"프레임 {len(frames)}개 발견 — 회계 화면은 보통 iframe 안에 있습니다.")
        out.append("")

    found = False
    for i, fr in enumerate(frames):
        try:
            url = (fr.url or "")[:70]
        except Exception:  # noqa: BLE001
            url = ""
        label = "메인" if i == 0 else f"iframe#{i}: {url}"
        lines = _inspect_frame(fr, label, limit)
        if lines:
            found = True
            out += lines
    if not found:
        out.append("(요소를 찾지 못했습니다 — 화면이 다 뜬 뒤 엔터를 눌러 보세요)")
    out.append("★ 표시는 '엑셀/다운로드/조회/거래처/기간' 같은 단어가 걸린 항목입니다.")
    return out
