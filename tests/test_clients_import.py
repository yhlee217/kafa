"""채워 온 조사표 넣기 — 옛 형식(.xls)·담당자 표기(유/무, 법)·합치기·저장 위치. 합성 데이터만."""
import yaml

from kafa.clients import parse_template
from kafa.clients_cli import main as clients_main
from kafa.config_loader import client_profile, load_clients, local_clients_path


def _xls(path, rows):
    """엑셀에서 '다른 이름으로 저장 → Excel 97-2003' 한 것과 같은 .xls."""
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("수임처")
    for r, row in enumerate([["수임처 이름", "개인/법인", "직원 있나요?", "업종(선택)", "비고(선택)"]]
                            + rows):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    wb.add_sheet("작성방법").write(0, 0, "작성 방법")
    wb.save(str(path))
    return path


ROWS = [
    ["가나상사", "법", "유", "", "11/26 폐업"],
    ["다라식당", "개인", "무", "", ""],
    ["마바/스튜디오", "개인", "유", "", "식대는 촬영비로 구분", "", "법인전환"],
    ["사아유통", "개인", "글쎄", "", ""],
]


def test_reads_xls_and_fathers_notation(tmp_path):
    issues = []
    got = parse_template(_xls(tmp_path / "조사표.xls", ROWS), issues=issues)
    assert got["가나상사"] == {"client_type": "corporate", "has_employees": True,
                            "note": "11/26 폐업"}
    assert got["다라식당"]["has_employees"] is False
    # 폴더 이름 규칙(safe_name)으로 저장 — 파이프라인이 찾는 이름과 같아야 한다
    assert "마바_스튜디오" in got and "마바/스튜디오" not in got
    # 비고 오른쪽 칸 메모도 남긴다
    assert got["마바_스튜디오"]["note"] == "식대는 촬영비로 구분 / 법인전환"
    # 모르는 표기는 비운 것으로 보고 알린다
    assert "has_employees" not in got["사아유통"]
    assert issues == [(5, "직원 있나요?", "글쎄")]


def test_import_merges_into_local_store_and_keeps_master_values(tmp_path, capsys):
    store = local_clients_path()
    store.parent.mkdir(parents=True)
    store.write_text(yaml.safe_dump({
        "defaults": {"client_type": "corporate", "has_employees": True},
        "clients": {"다라식당": {"name": "다라식당", "client_type": "individual"},
                    "자차상회": {"name": "자차상회", "client_type": "individual"}},
    }, allow_unicode=True), encoding="utf-8")

    assert clients_main(["import", str(_xls(tmp_path / "조사표.xls", ROWS)),
                         "--db", str(tmp_path / "없음.db")]) == 0
    data = yaml.safe_load(store.read_text(encoding="utf-8"))
    c = data["clients"]
    assert c["다라식당"] == {"name": "다라식당", "client_type": "individual",
                          "has_employees": False}
    assert c["자차상회"] == {"name": "자차상회", "client_type": "individual"}   # 안 지운다
    assert data["defaults"] == {"client_type": "corporate", "has_employees": True}

    said = capsys.readouterr().out
    assert "개인 3 / 법인 1 / 빈칸 0" in said
    assert "직원 있음 2 / 없음 1 / 빈칸 1" in said
    assert "5행 직원 있나요?: '글쎄'" in said
    for name in ("가나상사", "다라식당", "마바", "사아유통"):
        assert name not in said                           # 이름은 화면에 안 나온다

    # 파이프라인이 읽는 값이 바로 바뀐다
    assert client_profile("다라식당")["has_employees"] is False


def test_first_import_carries_over_values_from_old_repo_file(tmp_path, monkeypatch):
    """예전엔 레포의 config/clients.yaml 에 적었다 — 처음 저장할 때 그 값을 이어받는다."""
    from kafa import config_loader
    old = tmp_path / "repo_config"
    old.mkdir()
    (old / "clients.yaml").write_text(yaml.safe_dump({
        "defaults": {"client_type": "corporate", "has_employees": True},
        "clients": {"자차상회": {"client_type": "individual", "has_employees": False}},
    }, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(config_loader, "_DEFAULT_CONFIG_DIR", old)
    assert not local_clients_path().exists()

    assert clients_main(["import", str(_xls(tmp_path / "조사표.xls", ROWS[:1])),
                         "--db", str(tmp_path / "없음.db")]) == 0
    c = yaml.safe_load(local_clients_path().read_text(encoding="utf-8"))["clients"]
    assert c["자차상회"]["has_employees"] is False and "가나상사" in c


def test_loader_reads_local_store_over_repo_defaults(tmp_path):
    store = local_clients_path()
    store.parent.mkdir(parents=True)
    store.write_text(yaml.safe_dump({"clients": {"다라식당": {"has_employees": False}}},
                                    allow_unicode=True), encoding="utf-8")
    load_clients.cache_clear()
    prof = client_profile("다라식당")
    assert prof["has_employees"] is False
    assert prof["client_type"] == "corporate"        # 레포 기본값은 그대로


def test_from_master_writes_local_store_by_default(tmp_path):
    import openpyxl
    master = tmp_path / "마스터.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["회사명", "구분"])
    ws.append(["다라식당", "개인"])
    wb.save(master)
    assert clients_main(["from-master", str(master)]) == 0
    c = yaml.safe_load(local_clients_path().read_text(encoding="utf-8"))["clients"]
    assert c["다라식당"]["client_type"] == "individual"


def test_import_counts_names_that_match_processed_clients(tmp_path, capsys):
    from kafa.store.db import VoucherStore
    db = tmp_path / "kafa.db"
    store = VoucherStore(db)
    store.upsert_client("가나상사")
    store.upsert_client("마바_스튜디오")
    store.close()
    assert clients_main(["import", str(_xls(tmp_path / "조사표.xls", ROWS)),
                         "--db", str(db)]) == 0
    said = capsys.readouterr().out
    assert "처리 이력의 수임처와 이름이 맞는 곳: 2/4 (처리 이력 2곳)" in said
