"""아버님 PC 용 — 처리하기 → (클로드) → 답지 → 업로드본 만들기 → 다음 달에 배운 계정. 합성 데이터만."""
import json
import sqlite3

import openpyxl
import pytest

from kafa import desk
from kafa.desk import (
    CLAUDE,
    PROMPT_FILE,
    QUESTION_FILE,
    DeskError,
    Paths,
    extract_answers,
    finish,
    prepare,
    setup,
)

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _done(vendor="행복문구", bizno="111-11-11119", 일자="03-01"):
    return ["2026", 일자, "A", vendor, "법인", "", 10000, 1000, 0, 11000, "공제",
            "도매", "문구", "카과", "(판)소모품비", "", "", "", bizno]


def _todo(vendor="가나다인쇄소", bizno="222-22-22227", 업태="제조", 종목="인쇄", 일자="03-15",
          합계=22000):
    return ["2026", 일자, "A", vendor, "법인", "", 합계 - 합계 // 11, 합계 // 11, 0, 합계,
            "공제", 업태, 종목, "카과", "", "", "", "미추천", bizno]


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "kafa"
    Paths(b).ensure()
    _xlsx(b / "1_받은자료" / "고객A" / "3월.xlsx",
          [_done(), _todo(), _todo(vendor="라마바약국", bizno="333-33-33336", 업태="보건",
                                   종목="약국", 일자="03-20", 합계=5500)])
    return b


def _sheet(base):
    wb = openpyxl.load_workbook(base / "3_답지" / "답지.xlsx")
    ws = wb["답지"]
    head = [c.value for c in ws[1]]
    rows = [dict(zip(head, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    return wb, ws, head, rows


def _set(base, vendor, **cells):
    wb, ws, head, _ = _sheet(base)
    for r in ws.iter_rows(min_row=2):
        if r[head.index("거래처")].value == vendor:
            for k, v in cells.items():
                r[head.index(k)].value = v
    wb.save(base / "3_답지" / "답지.xlsx")


# ── 처리하기 ──

def test_prepare_writes_sheet_and_claude_files(base):
    rep = prepare(base)
    assert rep.files == 1 and rep.rows == 2 and rep.unresolved == 2 and rep.questions == 2
    wb, ws, _, rows = _sheet(base)
    assert {r["거래처"] for r in rows} == {"가나다인쇄소", "라마바약국"}     # 처리된 행은 안 나온다
    assert all(r["수임처"] == "고객A" and r["파일"] == "고객A/3월.xlsx" for r in rows)
    assert ws.column_dimensions["P"].hidden and ws.column_dimensions["Q"].hidden
    assert "계정목록" in wb.sheetnames and ws.data_validations.dataValidation
    # 클로드 파일: 질문지(이름·사업자번호·금액 없음)와 붙여넣을 말
    q = (base / CLAUDE / QUESTION_FILE).read_text(encoding="utf-8")
    for pii in ("가나다인쇄소", "222-22-22227", "고객A", "22000"):
        assert pii not in q
    assert "카페" in (base / CLAUDE / PROMPT_FILE).read_text(encoding="utf-8")   # 판단 기준
    assert (base / "1_받은자료" / "고객A" / "3월.xlsx").exists()                # 원본 그대로


def test_claude_answer_text_fills_the_sheet(base):
    prepare(base)
    qs = json.loads((base / CLAUDE / QUESTION_FILE).read_text(encoding="utf-8"))["questions"]
    qid = next(q["id"] for q in qs if q["종목"] == "인쇄")
    # 클로드 대화창에서 복사해 메모장에 붙인 글(설명 + 코드 블록)
    (base / CLAUDE / "클로드_답.txt").write_text(
        "답입니다.\n```json\n" + json.dumps({"answers": [
            {"id": qid, "code": 826, "confidence": 0.9, "why": "인쇄물"}]}) + "\n```\n",
        encoding="utf-8")
    rep = prepare(base)
    assert rep.claude_notes and "계정 1건" in rep.claude_notes[0]
    assert not (base / CLAUDE / "클로드_답.txt").exists()                     # _반영됨 으로
    _, _, _, rows = _sheet(base)
    printing = next(r for r in rows if r["거래처"] == "가나다인쇄소")
    assert printing["확정 계정"] == "(판)도서인쇄비" and printing["근거"].startswith("AI(Claude)")
    assert rep.ai_filled == 1 and rep.questions == 1                          # 약국만 남음


def test_reprepare_keeps_fathers_edits(base):
    prepare(base)
    _set(base, "라마바약국", **{"확정 계정": "(판)복리후생비", "메모": "직원 약"})
    rep = prepare(base)
    _, _, _, rows = _sheet(base)
    r = next(r for r in rows if r["거래처"] == "라마바약국")
    assert r["확정 계정"] == "(판)복리후생비" and r["메모"] == "직원 약" and rep.kept == 1


# ── 업로드본 만들기 ──

def test_finish_makes_upload_and_learns(base):
    prepare(base)
    _set(base, "가나다인쇄소", **{"확정 계정": "도서인쇄비"})        # 이름 일부도 받는다
    _set(base, "라마바약국", **{"올리지 않음": "예"})
    rep = finish(base)
    assert rep.result.ok and rep.answered == 1 and rep.excluded == 1
    out = base / "4_업로드본" / "고객A" / "2026-03"
    assert (out / "3월_upload.xls").exists() and (out / "3월_upload_review.txt").exists()
    assert rep.result.outcomes[0].written == 2 and rep.result.outcomes[0].skipped == 1
    assert rep.archived and rep.archived.exists() and not (base / "3_답지" / "답지.xlsx").exists()
    assert not (base / "1_받은자료" / "고객A" / "3월.xlsx").exists()            # 보관함으로

    con = sqlite3.connect(base / "_내부" / "kafa.db")
    got = dict(con.execute("SELECT 거래처, 차변계정코드 FROM vouchers WHERE skipped=0").fetchall())
    verdict = con.execute("SELECT 판정유형 FROM vouchers WHERE 거래처='가나다인쇄소'").fetchone()[0]
    skip = con.execute("SELECT skip_reason FROM vouchers WHERE 거래처='라마바약국'").fetchone()[0]
    con.close()
    assert got["가나다인쇄소"] == 826 and verdict == "rule_confirmed"          # 아버님 답 = 처리된 행
    assert skip == "담당자제외"

    # 다음 달 — 같은 가맹점은 아버님이 고른 계정이 미리 채워진다
    _xlsx(base / "1_받은자료" / "고객A" / "4월.xlsx", [_todo(일자="04-02")])
    prepare(base)
    _, _, _, rows = _sheet(base)
    assert rows[0]["거래처"] == "가나다인쇄소" and rows[0]["확정 계정"] == "(판)도서인쇄비"


def test_finish_refuses_unknown_account_and_keeps_everything(base):
    prepare(base)
    _set(base, "가나다인쇄소", **{"확정 계정": "촬영비"})
    with pytest.raises(DeskError) as e:
        finish(base)
    assert "촬영비" in str(e.value)
    assert (base / "1_받은자료" / "고객A" / "3월.xlsx").exists()
    assert (base / "3_답지" / "답지.xlsx").exists()


def test_changed_file_is_not_processed_with_old_answers(base):
    prepare(base)
    _set(base, "가나다인쇄소", **{"확정 계정": "(판)도서인쇄비", "합계": 99999})
    rep = finish(base)
    assert not rep.result.ok and "답지와 파일 내용이 다릅니다" in next(iter(rep.result.failures.values()))
    assert (base / "1_받은자료" / "고객A" / "3월.xlsx").exists()
    assert (base / "3_답지" / "답지.xlsx").exists()                            # 고쳐서 다시


def test_finish_without_sheet_says_what_to_do(tmp_path):
    with pytest.raises(DeskError) as e:
        finish(tmp_path / "kafa")
    assert "처리하기" in str(e.value)


# ── 처음 한 번 ──

def test_setup_makes_folders_launchers_and_imports(tmp_path):
    from kafa.store.db import VoucherStore
    src_db = tmp_path / "kafa.db"
    store = VoucherStore(src_db)
    store.upsert_client("고객A")
    store.upsert_client("고객B")
    store.close()
    src_clients = tmp_path / "clients.yaml"
    src_clients.write_text("clients:\n  고객A: {client_type: individual}\n", encoding="utf-8")

    b = tmp_path / "kafa"
    notes = setup(b, import_db=src_db, import_clients=src_clients, python=r"C:\Python312\python.exe")
    assert (b / "_내부" / "kafa.db").exists()
    assert (b / "1_받은자료" / "고객A").is_dir() and (b / "1_받은자료" / "고객B").is_dir()
    bat = (b / "처리하기.bat").read_bytes()
    assert b"\r\n" in bat and br"C:\Python312\python.exe" in bat
    assert b"kafa.desk_cli prepare" in bat and b"chcp 65001" in bat
    assert b"kafa.desk_cli finish" in (b / "업로드본_만들기.bat").read_bytes()
    from kafa.config_loader import client_profile
    assert client_profile("고객A")["client_type"] == "individual"
    assert any("수임처 2곳" in n for n in notes)
    # 두 번째 setup 은 이력을 덮어쓰지 않는다
    assert any("그대로" in n for n in setup(b, import_db=src_db))


def test_extract_answers_variants():
    body = {"answers": [{"id": "a", "code": 830}]}
    assert extract_answers(json.dumps(body)) == body
    assert extract_answers(json.dumps(body["answers"])) == body
    assert extract_answers("설명\n```json\n" + json.dumps(body) + "\n```") == body
    assert extract_answers("앞말 " + json.dumps(body) + " 뒷말") == body
    assert extract_answers("답이 없습니다") is None


def test_cli_prepare_and_finish(base, capsys):
    from kafa.desk_cli import main
    assert main(["prepare", "--base", str(base)]) == 0
    said = capsys.readouterr().out
    assert "확인할 건 2건" in said and "클로드에게 물을 것이 2가지" in said
    _set(base, "가나다인쇄소", **{"확정 계정": "(판)도서인쇄비"})
    assert main(["finish", "--base", str(base)]) == 0
    assert "업로드본" in capsys.readouterr().out
    assert desk.find_sheet(Paths(base)) is None


def test_fathers_type_goes_up_and_is_learned_next_month(tmp_path):
    """확정 유형 — 아버님이 고른 유형으로 올라가고, 처리 이력에 '담당자'로 남아 다음 달 같은 가맹점에 쓰인다."""
    b = tmp_path / "kafa"
    Paths(b).ensure()
    _xlsx(b / "1_받은자료" / "고객A" / "3월.xlsx",
          [_todo(일자="03-15"), _todo(일자="03-16")])          # 위하고 기본값은 카과(국세청 공제)
    prepare(b)
    _, _, _, rows = _sheet(b)
    assert {r["추천 유형"] for r in rows} == {"카과"} and {r["확정 유형"] for r in rows} == {"카과"}
    _set(b, "가나다인쇄소", **{"확정 계정": "(판)도서인쇄비", "확정 유형": "일반"})
    assert finish(b).result.ok

    con = sqlite3.connect(b / "_내부" / "kafa.db")
    got = con.execute("SELECT 유형코드, 공제여부, 전표상태, 확정출처 FROM vouchers").fetchall()
    con.close()
    assert set(got) == {(3, "불공제", "담당자확정", "담당자")}

    _xlsx(b / "1_받은자료" / "고객A" / "4월.xlsx", [_todo(일자="04-02")])
    prepare(b)
    _, _, _, rows = _sheet(b)
    assert rows[0]["추천 유형"] == "일반" and rows[0]["확정 계정"] == "(판)도서인쇄비"


def test_type_cell_is_checked_and_ignored_without_an_account(base):
    prepare(base)
    _set(base, "가나다인쇄소", **{"확정 계정": "(판)도서인쇄비", "확정 유형": "공제"})
    with pytest.raises(DeskError) as e:
        finish(base)
    assert "확정 유형" in str(e.value) and "카과" in str(e.value)
