"""실행 로그 — 수임처 이름이 번호로 가려지는지."""
from kafa.fetch.cli import _RunLog


def test_names_replaced_by_numbers(tmp_path):
    log = _RunLog(tmp_path / "run.log")
    log.name_of("(주)행복상사")
    log.name_of("튼튼상사")
    log.add("  [저장] (주)행복상사/2026")
    log.add("     ↳ [조회] 튼튼상사 화면에서 실패")
    out = log.write().read_text(encoding="utf-8")
    assert "행복상사" not in out and "튼튼상사" not in out
    assert "[저장] #1/2026" in out and "#2 화면에서 실패" in out
    assert "수임처 2곳" in out


def test_longer_name_masked_first(tmp_path):
    """'가나' 가 '가나다' 를 먼저 잡아먹지 않아야 한다."""
    log = _RunLog(tmp_path / "run.log")
    log.name_of("가나")
    log.name_of("가나다")
    log.add("[저장] 가나다/2026")
    out = log.write().read_text(encoding="utf-8")
    assert "가나다" not in out and "[저장] #2/2026" in out


def test_unknown_name_is_left_alone(tmp_path):
    log = _RunLog(tmp_path / "run.log")
    log.add("     · 주소로 이동")
    assert "주소로 이동" in log.write().read_text(encoding="utf-8")
