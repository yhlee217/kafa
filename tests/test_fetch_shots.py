"""화면 사진 — 값은 가리고 구조는 남긴다. 브라우저 없이 검증."""
import csv

from kafa.fetch.shots import ShotIndex, capture, safe_tag


class _Page:
    def __init__(self, fail=False):
        self.calls, self.shots, self.fail = [], [], fail

    def evaluate(self, js, arg=None):
        self.calls.append(("mask" if "kafa-mask-style" in js and "remove" not in
                           js.split("\n")[2] else "js", arg))
        if "blur" in js:
            self.calls[-1] = ("mask", arg)
        elif "data-kafa-masked" in js:
            self.calls[-1] = ("unmask", arg)
        return 1

    def screenshot(self, **kw):
        if self.fail:
            raise RuntimeError("no screen")
        self.shots.append(kw.get("path"))


_CFG = {"shot_mask_selectors": ["div#GRID_TOP", "canvas"]}


def test_masks_before_shooting_and_restores_after(tmp_path):
    pg = _Page()
    assert capture(pg, tmp_path / "a.png", _CFG, mask_texts=["행복상사"])
    kinds = [c[0] for c in pg.calls]
    assert kinds[0] == "mask" and kinds[-1] == "unmask"
    # 가릴 대상: 설정된 영역 + 거래처 이름
    assert pg.calls[0][1] == [["div#GRID_TOP", "canvas"], ["행복상사"]]
    assert pg.shots == [str(tmp_path / "a.png")]


def test_raw_mode_skips_masking(tmp_path):
    pg = _Page()
    capture(pg, tmp_path / "a.png", _CFG, mask_texts=["행복상사"], raw=True)
    assert [c[0] for c in pg.calls] == []          # 가리지 않는다
    assert pg.shots


def test_restores_even_when_screenshot_fails(tmp_path):
    pg = _Page(fail=True)
    assert capture(pg, tmp_path / "a.png", _CFG, mask_texts=["가"]) is False
    assert [c[0] for c in pg.calls][-1] == "unmask"   # 흐림을 반드시 되돌린다


def test_index_uses_numbers_in_filenames_names_only_in_csv(tmp_path):
    idx = ShotIndex(tmp_path)
    p1 = idx.add("(주)행복상사", "2026", "자료있음")
    p2 = idx.add("김아무개", "2026", "막힘: 조회")
    assert p1.name == "001_자료있음.png"
    assert "행복상사" not in p1.name and "김아무개" not in p2.name
    out = idx.write()
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == ["번호", "수임처", "기간", "결과"]
    assert rows[1][:2] == ["1", "(주)행복상사"]


def test_safe_tag_strips_path_characters():
    assert "/" not in safe_tag("막힘: 조회/다운로드")
    assert safe_tag("   ") == "결과"
