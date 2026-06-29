"""베이스 데이터 파이프라인 end-to-end — 라우팅·DB누적·아카이브·멱등·에러격리."""
import openpyxl

from kafa.pipeline.runner import run_pipeline

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _good_rows():
    return [
        ["2026", "03-15", "A", "카페", "법인", "커피", 5000, 500, 0, 5500,
         "공제", "음식점", "카페", "카과", "(판)복리후생비", "", "", "", "111-11-11119"],
        ["2026", "03-16", "A", "주유소", "법인", "경유", 50000, 5000, 0, 55000,
         "공제", "주유소", "경유", "카과", "(판)차량유지비", "", "", "", "111-11-11119"],
    ]


def test_pipeline_end_to_end(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx", _good_rows())

    res = run_pipeline(inbox, out)
    assert res.ok and len(res.outcomes) == 1
    o = res.outcomes[0]
    assert o.client == "고객001" and o.period == "2026-03"
    assert o.written == 2 and o.inserted == 2

    # 업로드본 + 신고전점검 산출물 생성(리포트는 <stem>_upload_* 규칙)
    base = out / "고객001" / "2026-03"
    assert (base / "3월_upload.xls").exists()
    assert (base / "3월_upload_prefile.txt").exists()
    # DB 누적
    assert res.total_in_db == 2 and (out / "kafa.db").exists()
    # 원본은 아카이브로 이동(인박스에서 사라짐)
    assert not (inbox / "고객001" / "3월.xlsx").exists()
    assert (out / "_archive" / "고객001" / "3월.xlsx").exists()
    # 로그 + 고객 진행 현황 보드(자동 갱신)
    assert (out / "_logs" / "manifest.json").exists()
    assert (out / "_board.html").exists() and (out / "_board.txt").exists()


def test_pipeline_idempotent_rerun(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx", _good_rows())
    run_pipeline(inbox, out)

    # 같은 파일을 다시 받아 재실행 → DB 총량 불변(멱등)
    _xlsx(inbox / "고객001" / "3월.xlsx", _good_rows())
    res2 = run_pipeline(inbox, out)
    assert res2.total_in_db == 2
    assert res2.outcomes[0].inserted == 0 and res2.outcomes[0].existing == 2


def test_pipeline_error_isolation(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx", _good_rows())
    # 형식이 잘못된 파일(다른 고객)
    bad = inbox / "고객002" / "bad.xlsx"
    bad.parent.mkdir(parents=True)
    wb = openpyxl.Workbook(); wb.active.append(["엉뚱", "컬럼"]); wb.save(bad)

    res = run_pipeline(inbox, out)
    assert not res.ok
    assert any("고객002" in k for k in res.failures)
    # 정상 고객은 그대로 처리
    assert (out / "고객001" / "2026-03" / "3월_upload.xls").exists()
    assert res.total_in_db == 2


def test_pipeline_multi_client(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx", _good_rows())
    _xlsx(inbox / "고객002" / "3월.xlsx", _good_rows())
    res = run_pipeline(inbox, out)
    assert res.ok and len(res.outcomes) == 2
    assert res.total_in_db == 4               # 고객별 별도 누적
    assert (out / "고객001" / "2026-03" / "3월_upload.xls").exists()
    assert (out / "고객002" / "2026-03" / "3월_upload.xls").exists()
