"""폴더 감시(폴링) — 안정 파일만 처리·중단 신호·반쯤 받은 파일 대기."""
import openpyxl

from kafa.pipeline.watch import watch

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(_COLS)
    ws.append(["2026", "03-15", "A", "카페", "법인", "커피", 5000, 500, 0, 5500,
               "공제", "음식점", "카페", "카과", "(판)복리후생비", "", "", "", "111-11-11119"])
    wb.save(path)
    return path


def test_watch_processes_stable_file(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx")
    runs = watch(inbox, out, max_cycles=3, sleep=lambda _: None,
                 on_event=lambda r: None)
    # 1회차=새 파일 대기, 2회차=안정→처리
    assert any(r.total_in_db == 1 for r in runs)
    assert (out / "고객001" / "2026-03" / "3월_upload.xls").exists()


def test_watch_first_cycle_waits(tmp_path):
    # 갓 생긴 파일은 첫 회차에 '받는 중'으로 보고 처리하지 않음
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx")
    runs = watch(inbox, out, max_cycles=1, sleep=lambda _: None,
                 on_event=lambda r: None)
    assert runs == []
    assert (inbox / "고객001" / "3월.xlsx").exists()   # 아직 처리 안 됨


def test_watch_stop_signal(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    runs = watch(inbox, out, stop=lambda: True, sleep=lambda _: None,
                 on_event=lambda r: None)
    assert runs == []


def test_watch_on_event_called(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx")
    seen = []
    watch(inbox, out, max_cycles=3, sleep=lambda _: None, on_event=seen.append)
    assert seen and seen[0].total_in_db == 1
