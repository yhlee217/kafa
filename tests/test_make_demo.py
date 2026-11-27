"""개발용 가짜 자료(tools/make_demo.py)가 처리하기·업로드본 만들기를 끝까지 통과하는지."""
import runpy
from pathlib import Path

from kafa.desk import finish, prepare

ROOT = Path(__file__).resolve().parents[1]


def test_demo_data_goes_through_the_desk_flow(tmp_path):
    make_demo = runpy.run_path(str(ROOT / "tools" / "make_demo.py"))["make_demo"]
    base = tmp_path / "kafa-demo"
    assert make_demo(base) == 8
    rep = prepare(base)
    assert rep.files == 2 and not rep.failures and rep.rows == 7      # 위하고가 채운 1건은 답지에 없음
    fin = finish(base)
    assert fin.result.ok and len(fin.result.outcomes) == 2
