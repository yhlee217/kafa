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


def _row(거래처, 차변계정, 일자="03-15", 전표상태="확정가능"):
    return ["2026", 일자, "A", 거래처, "법인", "커피", 5000, 500, 0, 5500,
            "공제", "음식점", "카페", "카과", 차변계정, "미지급비용", "", 전표상태,
            "111-11-11119"]


def test_db_history_resolves_next_month(tmp_path):
    """지난 달 DB 이력으로 이번 달 같은 가맹점의 미추천이 자동 해소된다."""
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    # 1월: 카페A 를 (판)복리후생비로 처리한 이력이 DB에 쌓임
    _xlsx(inbox / "고객001" / "1월.xlsx",
          [_row("카페A", "(판)복리후생비", "01-10")])
    r1 = run_pipeline(inbox, out)
    assert r1.ok and r1.total_in_db == 1

    # 2월: 같은 카페A 인데 미추천(차변계정 비어있음)
    _xlsx(inbox / "고객001" / "2월.xlsx",
          [_row("카페A", "", "02-10", 전표상태="미추천")])
    r2 = run_pipeline(inbox, out)
    assert r2.ok

    import sqlite3
    con = sqlite3.connect(out / "kafa.db")
    codes = [c for (c,) in con.execute(
        "SELECT 차변계정코드 FROM vouchers WHERE period='2026-02'")]
    con.close()
    assert codes == [811]          # 이력에서 복리후생비(811)로 해소


def test_db_history_is_per_client(tmp_path):
    """다른 고객의 이력은 섞이지 않는다."""
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "1월.xlsx", [_row("카페A", "(판)복리후생비", "01-10")])
    run_pipeline(inbox, out)
    # 고객002 에 같은 가맹점이 미추천으로 등장 → 고객001 이력을 쓰면 안 됨
    _xlsx(inbox / "고객002" / "2월.xlsx", [_row("카페A", "", "02-10", 전표상태="미추천")])
    run_pipeline(inbox, out)

    import sqlite3
    con = sqlite3.connect(out / "kafa.db")
    codes = [c for (c,) in con.execute(
        "SELECT 차변계정코드 FROM vouchers WHERE client_id='고객002'")]
    con.close()
    assert codes == [None]         # 다른 고객 이력은 미사용 → 미해소


def test_industry_rule_is_learned_per_client(tmp_path):
    """음식점 기준이 수임처마다 달라도, 각 고객 이력에서 각자 학습한다.

    고객A: 음식점 → 접대비(813) / 고객B: 음식점 → 복리후생비(811) 로 처리해온 경우,
    처음 보는 식당이 미추천으로 와도 각자 자기 기준으로 해소되어야 한다.
    """
    def 식당(거래처, 계정, 일자, 상태="확정가능"):
        return ["2026", 일자, "A", 거래처, "법인", "", 9000, 900, 0, 9900,
                "불공제", "음식점업", "한식", "일반", 계정, "미지급비용", "", 상태,
                "111-11-11119"]

    inbox, out = tmp_path / "inbox", tmp_path / "out"
    # 1월: 두 고객이 서로 다른 기준으로 음식점을 처리(각 3건 = min_support 충족)
    _xlsx(inbox / "고객A" / "1월.xlsx",
          [식당(f"식당A{i}", "(판)접대비(기업업무추진비)", f"01-1{i}") for i in range(3)])
    _xlsx(inbox / "고객B" / "1월.xlsx",
          [식당(f"식당B{i}", "(판)복리후생비", f"01-1{i}") for i in range(3)])
    assert run_pipeline(inbox, out).ok

    # 2월: 양쪽 모두 '처음 보는' 식당이 미추천으로 등장 → 가맹점 시드로는 못 푼다
    _xlsx(inbox / "고객A" / "2월.xlsx", [식당("새로운맛집", "", "02-10", "미추천")])
    _xlsx(inbox / "고객B" / "2월.xlsx", [식당("낯선식당", "", "02-10", "미추천")])
    assert run_pipeline(inbox, out).ok

    import sqlite3
    con = sqlite3.connect(out / "kafa.db")
    got = dict(con.execute(
        "SELECT client_id, 차변계정코드 FROM vouchers WHERE period='2026-02'"))
    con.close()
    assert got["고객A"] == 813      # 이 수임처는 접대비로 처리해왔음
    assert got["고객B"] == 811      # 이 수임처는 복리후생비로 처리해왔음
