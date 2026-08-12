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


def inspect_page(page, *, limit: int = 40) -> list[str]:
    """화면 요소 후보 목록(문자열 라인). 값(value)은 읽지 않는다."""
    out = ["", "── 현재 화면의 후보 요소 (selector 보정용) ──",
           "※ 아래 id/name/class 를 config/fetch/wehago.yaml 에 적으세요.",
           "   예) client_search_input: \"#custSearch\"  또는  \"input[name='custNm']\"", ""]
    for label, css in _PROBES:
        try:
            els = page.query_selector_all(css)
        except Exception:  # noqa: BLE001 — 화면 상태에 따라 실패해도 계속
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
        rows.sort(key=lambda r: not r[0])          # 키워드 매칭 우선 노출
        out.append(f"[{label}] {len(els)}개")
        out += [r[1] for r in rows[:limit]]
        if len(rows) > limit:
            out.append(f"   … 외 {len(rows) - limit}개")
        out.append("")
    out.append("★ 표시는 '엑셀/다운로드/조회/거래처/기간' 같은 단어가 걸린 항목입니다.")
    return out
