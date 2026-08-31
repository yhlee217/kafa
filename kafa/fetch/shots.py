"""화면 사진 남기기 — AI/사람이 '이게 무슨 화면인지' 판단할 수 있게.

DOM 만으로는 예상 못 한 화면(처음 보는 팝업·권한 안내·점검 공지 등)을 알 수 없다.
그래서 한 바퀴 돌 때 화면 사진을 남긴다.

**원천 데이터는 가린다**(보안 제0원칙). 거래 내역 표·거래처 이름처럼 값이 보이는 곳은
흐리게 처리한 뒤 찍는다. 화면 구조·버튼·팝업 글자는 그대로 남아 판단에 지장이 없다.
가리지 않은 원본이 필요하면 `--shots-raw` 로 명시해야 한다(사람이 눈으로만 볼 것).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

# 값이 보이는 영역을 흐리게. 지운 뒤 원상복구한다.
_MASK_JS = r"""
([selectors, texts]) => {
  const old = document.getElementById('kafa-mask-style');
  if (old) old.remove();
  const st = document.createElement('style');
  st.id = 'kafa-mask-style';
  const sels = (selectors || []).filter(Boolean);
  st.textContent = sels.length
    ? sels.join(',') + '{filter:blur(14px)!important}'
    : '';
  document.head.appendChild(st);
  // 거래처 이름처럼 글자로 박힌 값도 가린다.
  const marked = [];
  for (const t of (texts || [])) {
    if (!t || t.length < 2) continue;
    for (const el of document.querySelectorAll('a,span,div,td,button,h1,h2,li')) {
      if (el.children.length > 1) continue;
      if ((el.textContent || '').includes(t)) {
        el.setAttribute('data-kafa-masked', '1');
        el.style.filter = 'blur(10px)';
        marked.push(el);
      }
    }
  }
  return marked.length;
}
"""

_UNMASK_JS = r"""
() => {
  const st = document.getElementById('kafa-mask-style');
  if (st) st.remove();
  for (const el of document.querySelectorAll('[data-kafa-masked]')) {
    el.style.filter = '';
    el.removeAttribute('data-kafa-masked');
  }
  return true;
}
"""

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_tag(text: str) -> str:
    """파일 이름에 쓸 수 있게 정리(값은 담지 않는다 — 결과 갈래만 넣는다)."""
    return _UNSAFE.sub("_", (text or "").strip()).strip(". ") or "결과"


def capture(page, path, cfg: dict, *, mask_texts=(), raw: bool = False) -> bool:
    """화면 사진 저장. raw 가 아니면 값이 보이는 곳을 흐리게 한 뒤 찍는다."""
    sels = list((cfg or {}).get("shot_mask_selectors") or [])
    texts = [t for t in mask_texts if t]
    masked = False
    try:
        if not raw and (sels or texts):
            page.evaluate(_MASK_JS, [sels, texts])
            masked = True
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=False)
        return True
    except Exception:  # noqa: BLE001 — 사진을 못 찍어도 수집은 계속
        return False
    finally:
        if masked:
            try:
                page.evaluate(_UNMASK_JS)
            except Exception:  # noqa: BLE001
                pass


class ShotIndex:
    """사진 번호 ↔ 수임처. 이름은 **로컬 CSV 에만** 남기고, 대화에는 번호만 쓴다."""

    def __init__(self, directory, *, raw: bool = False):
        self.dir = Path(directory)
        self.raw = raw
        self.rows: list[tuple[int, str, str, str]] = []

    def add(self, client: str, period: str, kind: str) -> Path:
        n = len(self.rows) + 1
        name = f"{n:03d}_{safe_tag(kind)}.png"
        self.rows.append((n, client, period, kind))
        return self.dir / name

    def write(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        out = self.dir / "index.csv"
        with out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["번호", "수임처", "기간", "결과"])
            for n, client, period, kind in self.rows:
                w.writerow([n, client, period, kind])
        return out
