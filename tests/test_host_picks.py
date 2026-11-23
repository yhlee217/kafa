"""Claude 추천을 운영에 붙이기 — 질문지 ↔ 답지 왕복(kafa-ask). 합성 데이터만."""
import json

import openpyxl
import pytest

from kafa.pipeline.ask import prepare_questions
from kafa.pipeline.runner import run_pipeline
from kafa.recommend.features import make_features, scrub_item
from kafa.recommend.host_picks import (
    ANSWERS,
    BASIS_PREFIX,
    EXAM_KEY,
    EXAM_QUESTIONS,
    HINT_PREFIX,
    QUESTIONS,
    ai_dir,
    build_exam,
    exam_key_path,
    load_picks,
    merge_answers,
    pick_signature,
    score_exam,
)

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]

VENDOR = "가나다인쇄소"          # 합성 가맹점(처음 보는 곳)
BIZNO = "222-22-22227"


def _xlsx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _done(vendor="행복문구", bizno="111-11-11119", 일자="03-01"):
    """처리된 행 — 차변계정이 채워져 있다(이력)."""
    return ["2026", 일자, "A", vendor, "법인", "", 10000, 1000, 0, 11000, "공제",
            "도매", "문구", "카과", "(판)소모품비", "", "", "", bizno]


def _todo(vendor=VENDOR, bizno=BIZNO, 업태="제조", 종목="인쇄", 품명="", 일자="03-15",
          합계=22000):
    """처리 전 행 — 위하고가 계정을 못 정했다(미추천)."""
    return ["2026", 일자, "A", vendor, "법인", 품명, 합계 - 합계 // 11, 합계 // 11, 0, 합계,
            "공제", 업태, 종목, "카과", "", "", "", "미추천", bizno]


def _inbox(tmp_path, rows):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    _xlsx(inbox / "고객001" / "3월.xlsx", rows)
    return inbox, out


def _answer(out, answers):
    (ai_dir(out) / ANSWERS).write_text(json.dumps({"answers": answers}, ensure_ascii=False),
                                       encoding="utf-8")


def _questions(out):
    return json.loads((ai_dir(out) / QUESTIONS).read_text(encoding="utf-8"))


# ── 품명 가리기 ──

@pytest.mark.parametrize("item,vendor,want", [
    ("경유", "", "경유"),
    ("스타벅스 아메리카노", "(주)스타벅스", "(가맹점) 아메리카노"),
    ("스타벅스", "스타벅스코리아", "(가맹점)"),
    ("주문 1234-5678-9012", "쿠팡", "주문 #"),
    ("김밥", "김밥천국", "김밥"),                  # 두 글자 일반명사는 남긴다
    ("스타 벅스 라떼", "스타벅스", "(가맹점)"),   # 띄어 쓴 가맹점명 — 통째로
])
def test_scrub_item(item, vendor, want):
    assert scrub_item(item, vendor) == want


def test_features_carry_no_vendor_or_bizno():
    f = make_features(업태="제조", 종목="인쇄", 품명=f"{VENDOR} 명함 {BIZNO}", 유형="카과",
                      거래처=VENDOR)
    blob = json.dumps(f, ensure_ascii=False)
    assert VENDOR not in blob and BIZNO not in blob and "22222" not in blob
    assert set(f) == {"업태", "종목", "품명", "유형"}


# ── 질문지 ──

def test_prepare_collects_only_unresolved_and_hides_pii(tmp_path):
    rows = [_done(),
            _todo(), _todo(일자="03-16"),                               # 같은 특징 2건 → 1문항
            _todo(vendor="라마바카페", bizno="333-33-33336", 업태="음식점", 종목="커피"),
            _todo(vendor="빈칸상점", bizno="444-44-44445", 업태="", 종목=""),   # 물을 거리 없음
            _todo(vendor="토스페이먼츠", bizno="555-55-55554", 업태="서비스", 종목="전자결제")]
    inbox, out = _inbox(tmp_path, rows)

    res = prepare_questions(inbox, out)
    qs = res.questions
    assert res.new == 2 and qs.new_rows == 3
    assert qs.no_info_rows == 1 and qs.agent_rows == 1
    # 원본은 그대로(처리 단계가 아니다)
    assert (inbox / "고객001" / "3월.xlsx").exists()
    assert not (out / "고객001" / "_state" / "dup.json").exists()

    raw = (ai_dir(out) / QUESTIONS).read_text(encoding="utf-8")
    for pii in (VENDOR, BIZNO, "라마바카페", "고객001", "22000"):
        assert pii not in raw
    data = json.loads(raw)
    top = data["questions"][0]
    assert (top["업태"], top["종목"], top["rows"]) == ("제조", "인쇄", 2)
    # 계정 목록은 이 사무실이 쓰는 계정(이력)만 — 이번엔 DB 가 비어 있어 전체 허용 목록
    assert any(a["code"] == 830 for a in data["accounts"])
    assert all("used" not in a for a in data["accounts"])


def test_prepare_skips_rows_history_already_solves(tmp_path):
    """같은 가맹점을 이 파일에서 이미 처리했다면 시드가 풀어 준다 — 묻지 않는다."""
    inbox, out = _inbox(tmp_path, [_done(vendor=VENDOR, bizno=BIZNO), _todo()])
    res = prepare_questions(inbox, out)
    assert res.new == 0 and res.questions.unresolved_rows == 0


# ── 답지 합치기 ──

def test_merge_validates_and_archives(tmp_path):
    inbox, out = _inbox(tmp_path, [_todo(),
                                   _todo(vendor="라마바카페", bizno="333-33-33336",
                                         업태="음식점", 종목="커피"),
                                   _todo(vendor="사아자상회", bizno="666-66-66663",
                                         업태="소매", 종목="잡화")])
    prepare_questions(inbox, out)
    ids = {(q["업태"], q["종목"]): q["id"] for q in _questions(out)["questions"]}
    _answer(out, [
        {"id": ids[("제조", "인쇄")], "code": 826, "confidence": 0.9, "why": "인쇄물 제작"},
        {"id": ids[("음식점", "커피")], "code": "(판)복리후생비", "confidence": 0.7},
        {"id": ids[("소매", "잡화")], "code": None, "why": "잡화만으로는 모름"},
        {"id": "없는번호", "code": 830},
        {"id": ids[("제조", "인쇄")], "code": 262},        # 대변 쪽 계정 — 받지 않는다
    ])
    res = merge_answers(out)
    assert res.accepted == 2 and res.unknown == 1
    assert {why for _, why in res.rejected} == {"질문지에 없는 id", "허용되지 않은 계정코드 262"}
    assert not (ai_dir(out) / ANSWERS).exists() and res.archived.exists()

    picks = load_picks(out)
    p = picks[ids[("제조", "인쇄")]]
    assert p["code"] == 826 and p["name"] == "(판)도서인쇄비" and p["rows"] == 1
    assert picks[ids[("음식점", "커피")]]["code"] == 811
    assert picks[ids[("소매", "잡화")]]["code"] is None

    # 한 번 물은 것(모름 포함)은 다시 묻지 않는다
    again = prepare_questions(inbox, out)
    assert again.new == 0
    assert again.questions.asked_rows == 1            # 모름 — 담당자에게 남는다


def test_broken_answer_file_is_kept_for_fixing(tmp_path):
    inbox, out = _inbox(tmp_path, [_todo()])
    prepare_questions(inbox, out)
    (ai_dir(out) / ANSWERS).write_text("{not json", encoding="utf-8")
    res = merge_answers(out)
    assert res.rejected and res.archived is None
    assert (ai_dir(out) / ANSWERS).exists()


# ── 파이프라인 적용 ──

def test_pipeline_applies_answers(tmp_path):
    inbox, out = _inbox(tmp_path, [_todo(), _todo(일자="03-16"),
                                   _todo(vendor="라마바카페", bizno="333-33-33336",
                                         업태="음식점", 종목="커피")])
    prepare_questions(inbox, out)
    ids = {(q["업태"], q["종목"]): q["id"] for q in _questions(out)["questions"]}
    _answer(out, [
        {"id": ids[("제조", "인쇄")], "code": 826, "confidence": 0.95, "why": "인쇄물"},
        {"id": ids[("음식점", "커피")], "code": 811, "confidence": 0.3},   # 확신 낮음
    ])
    res = run_pipeline(inbox, out)          # 답지는 파이프라인이 먼저 합친다
    assert res.ok and res.ai_note.startswith("Claude 답 반영: 계정 2건")
    assert res.outcomes[0].ai_applied == 2

    import sqlite3
    con = sqlite3.connect(out / "kafa.db")
    got = con.execute("SELECT 업태, 차변계정코드, 판정유형, 신뢰도, 추천근거 FROM vouchers "
                      "ORDER BY 거래일자").fetchall()
    con.close()
    printing = [g for g in got if g[0] == "제조"]
    assert all(g[1] == 826 and g[2] == "recommended" for g in printing)
    assert all(g[3] == 0.7 and g[4] == BASIS_PREFIX + "인쇄물" for g in printing)   # 상한
    cafe = [g for g in got if g[0] == "음식점"][0]
    assert cafe[1] is None and cafe[2] == "unresolved"     # 확신 0.3 < 0.5 — 적용 안 함
    assert cafe[4] == HINT_PREFIX + "(판)복리후생비 (확신 0.3 — 적용 안 함)"   # 담당자 참고용


def test_claude_guesses_do_not_become_history(tmp_path):
    """이번 달 Claude 추정이 다음 달 '같은 가맹점 이력'(더 높은 신뢰도)으로 둔갑하지 않는다."""
    from kafa.store.db import VoucherStore
    inbox, out = _inbox(tmp_path, [_done(), _todo()])
    prepare_questions(inbox, out)
    qid = _questions(out)["questions"][0]["id"]
    _answer(out, [{"id": qid, "code": 826, "confidence": 0.9}])
    assert run_pipeline(inbox, out).outcomes[0].ai_applied == 1
    db = VoucherStore(out / "kafa.db")
    vendors = {r[0] for r in db.seed_records("고객001")}
    db.close()
    assert vendors == {"행복문구"}                   # 처리된 행만 — Claude 추정 행은 빠진다

    # 다음 달 같은 가맹점 — 여전히 'AI 추정'으로 표시돼 들어간다(이력으로 세탁되지 않음)
    _xlsx(inbox / "고객001" / "4월.xlsx", [_todo(일자="04-02")])
    res = run_pipeline(inbox, out)
    assert res.ok and res.outcomes[0].ai_applied == 1


def test_unknown_answer_leaves_a_note(tmp_path):
    inbox, out = _inbox(tmp_path, [_todo(업태="보건", 종목="약국")])
    prepare_questions(inbox, out)
    qid = _questions(out)["questions"][0]["id"]
    _answer(out, [{"id": qid, "code": None, "why": "업무무관 의심 — 담당자 판단"}])
    res = run_pipeline(inbox, out)
    assert res.ok and res.outcomes[0].ai_applied == 0
    import sqlite3
    con = sqlite3.connect(out / "kafa.db")
    basis, verdict = con.execute("SELECT 추천근거, 판정유형 FROM vouchers").fetchone()
    con.close()
    assert verdict == "unresolved"
    assert basis == HINT_PREFIX + "모름 — 업무무관 의심 — 담당자 판단"


def test_history_comes_before_claude(tmp_path):
    """이 수임처가 그 가맹점을 처리한 이력이 있으면 Claude 답보다 이력이 먼저다."""
    inbox, out = _inbox(tmp_path, [_todo()])
    prepare_questions(inbox, out)
    qid = _questions(out)["questions"][0]["id"]
    _answer(out, [{"id": qid, "code": 826, "confidence": 0.9}])
    merge_answers(out)

    _xlsx(inbox / "고객001" / "3월.xlsx",
          [_done(vendor=VENDOR, bizno=BIZNO), _todo(일자="03-20")])
    res = run_pipeline(inbox, out)
    assert res.ok and res.outcomes[0].ai_applied == 0


def test_agent_rows_never_get_claude_answer(tmp_path):
    """대행사 행은 업태·종목이 대행사의 것 — 같은 특징의 답이 있어도 쓰지 않는다."""
    from kafa.recommend.host_picks import HostPickRecommender
    from kafa.rules.models import InputRow
    feats = make_features(업태="서비스", 종목="광고", 유형="카과")
    picks = {pick_signature(feats): {"code": 831, "confidence": 0.9}}
    reco = HostPickRecommender(picks)
    agent = InputRow(거래처="토스페이먼츠", 업태="서비스", 종목="광고", 유형="카과")   # 이름으로
    other = InputRow(거래처="마바사", 업태="서비스", 종목="광고", 유형="카과")
    assert not reco.recommend_for(agent).resolved
    assert reco.recommend_for(other).account_code == 831


def test_same_industry_spellings_share_one_question():
    """업태 표기만 다른 것(소매업/도소매)은 한 질문 — config/industry.yaml 바구니."""
    a = pick_signature(make_features(업태="소매업", 종목="잡화", 유형="카과"))
    b = pick_signature(make_features(업태="도소매", 종목="잡화", 유형="카과"))
    assert a == b


# ── 시험(정확도 기준선) ──

def test_exam_and_score(tmp_path):
    out = tmp_path / "out"
    records = (
        [("문구점A", "도매", "문구", "", "카과", 830)] * 5
        + [("문구점B", "도매", "문구", "", "카과", 811)] * 1         # 갈리는 업종
        + [("인쇄소", "제조", "인쇄", "", "카과", 826)] * 3
        + [("카페", "음식점", "커피", "", "카과", 811)] * 2
        + [("토스페이먼츠", "서비스", "전자결제", "", "카과", 831)] * 9  # 대행사 — 빠진다
        + [("빈칸", "", "", "", "카과", 830)] * 4)                       # 물을 거리 없음
    n = build_exam(records, out, n=10)
    assert n == 3
    exam = json.loads((ai_dir(out) / EXAM_QUESTIONS).read_text(encoding="utf-8"))
    assert exam["kind"] == "kafa-exam" and "codes" not in json.dumps(exam)    # 정답 없음
    assert "토스페이먼츠" not in json.dumps(exam, ensure_ascii=False)
    assert not (ai_dir(out) / EXAM_KEY).exists()          # 정답은 공유 폴더(_ai) 밖에
    key = json.loads(exam_key_path(out).read_text(encoding="utf-8"))["key"]
    ids = {(q["업태"], q["종목"]): q["id"] for q in exam["questions"]}
    assert key[ids[("도매", "문구")]]["answer"] == 830

    (ai_dir(out) / "exam_answers.json").write_text(json.dumps({"answers": [
        {"id": ids[("도매", "문구")], "code": 811, "confidence": 0.6},   # 이력에 있는 계정
        {"id": ids[("제조", "인쇄")], "code": 826, "confidence": 0.9},   # 정답
        {"id": ids[("음식점", "커피")], "code": 511, "confidence": 0.8},  # (제)복리후생비
    ]}), encoding="utf-8")
    sc = score_exam(out)
    assert (sc.answered, sc.exact, sc.seen, sc.same_account) == (3, 1, 1, 1)
    assert sc.rows == 11 and sc.rows_exact == 3
    assert sc.bands["0.8 이상"] == (1, 2)
    # 행 단위: 문구 6건 중 811 은 1건, 인쇄 3건 모두, 커피 2건 중 511 은 0건 → 4/11
    assert (sc.rows_hit, sc.applied_hit, sc.applied_rows) == (4, 4, 11)
    assert sc.band_rows["0.8 이상"] == (3, 5) and sc.band_rows["0.5~0.8"] == (1, 6)
    assert "적용되는 답(확신 0.5 이상)  4/11건 (36%)" in sc.render()
    assert (ai_dir(out) / "exam_report.txt").exists()


# ── kafa-run 연결 ──

@pytest.fixture
def run_wired(tmp_path, monkeypatch):
    from kafa import clients_cli, run_cli
    from kafa.pipeline import cli as pipeline_cli
    monkeypatch.setattr(run_cli, "SETTINGS", tmp_path / "run.json")
    master = tmp_path / "master.xlsx"
    master.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(clients_cli, "main", lambda argv: calls.append("clients") or 0)
    monkeypatch.setattr(pipeline_cli, "main", lambda argv: calls.append("pipeline") or 0)
    inbox, out = _inbox(tmp_path, [_todo()])
    base = ["--master", str(master), "--inbox", str(inbox), "--out", str(out), "--skip-fetch"]
    return run_cli, base, calls, out


def test_run_waits_for_answers(run_wired, capsys):
    run_cli, base, calls, out = run_wired
    assert run_cli.main(base) == run_cli.ASK_WAIT
    assert calls == ["clients"]                         # 분류 전에 멈췄다
    said = capsys.readouterr().out
    assert "docs/cowork_ask.md" in said and "고객001" not in said and VENDOR not in said

    qid = _questions(out)["questions"][0]["id"]
    _answer(out, [{"id": qid, "code": 826, "confidence": 0.9}])
    assert run_cli.main(base) == 0                      # 답을 받으면 그대로 진행
    assert calls[-1] == "pipeline"


def test_run_no_ask_goes_straight_through(run_wired):
    run_cli, base, calls, _ = run_wired
    assert run_cli.main(base + ["--no-ask"]) == 0
    assert calls == ["clients", "pipeline"]


def test_ask_cli_prepare_prints_no_names(run_wired, capsys):
    from kafa import ask_cli
    _, base, _, out = run_wired
    inbox = base[base.index("--inbox") + 1]
    assert ask_cli.main(["prepare", "--inbox", inbox, "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "새로 물을 것 1가지" in said
    assert "고객001" not in said and VENDOR not in said and BIZNO not in said


def test_exam_key_from_old_location_still_scores_and_is_cleaned(tmp_path):
    """예전 위치(_ai/exam_key.json)에 만든 시험도 채점되고, 새 시험을 만들면 지워진다."""
    out = tmp_path / "out"
    records = [("인쇄소", "제조", "인쇄", "", "카과", 826)] * 3
    build_exam(records, out, n=5)
    exam_key_path(out).rename(ai_dir(out) / EXAM_KEY)     # 예전 배치 흉내
    qid = json.loads((ai_dir(out) / EXAM_QUESTIONS).read_text(encoding="utf-8"))["questions"][0]["id"]
    (ai_dir(out) / "exam_answers.json").write_text(
        json.dumps({"answers": [{"id": qid, "code": 826, "confidence": 0.9}]}), encoding="utf-8")
    assert score_exam(out).exact == 1
    build_exam(records, out, n=5)
    assert not (ai_dir(out) / EXAM_KEY).exists() and exam_key_path(out).exists()
