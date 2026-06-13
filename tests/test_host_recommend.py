"""호스트 모델(Claude Desktop/Code) 기반 추정 — analyze → convert(recommendations).

별도 API 키 없이 호스트 모델이 추정한 계정을 적용하는 경로. PII 미전송 검증 포함.
"""
import json
from decimal import Decimal

import openpyxl
import xlrd

from kafa.recommend.recommender import PickRecommender, SeedRecommender
from kafa.rules.models import ClassifiedRow, InputRow
from kafa.service import analyze, convert

_COLS = ["연도", "일자", "Code", "거래처", "구분", "품명", "공급가액", "세액", "비과세",
         "합계", "국세청", "업태", "종목", "유형", "차변계정", "대변계정", "관리",
         "전표상태", "사업자등록번호"]


def _xlsx(path, rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(_COLS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _row(raw_index):
    return InputRow(raw_index=raw_index)


# ── PickRecommender ──

def test_pick_applies_allowed_code():
    pr = PickRecommender({3: {"account_code": 811, "rationale": "카페 간식"}},
                         allowed_codes={811, 822})
    rec = pr.recommend_for(_row(3))
    assert rec.resolved and rec.account_code == 811
    assert rec.basis.startswith("AI(Claude) 추정:")


def test_pick_rejects_disallowed_code_falls_back():
    class FB(SeedRecommender):
        def recommend_for(self, row):
            from kafa.recommend.recommender import Recommendation
            return Recommendation(999, 0.5, "폴백", resolved=True)
    pr = PickRecommender({3: {"account_code": 12345}},  # 미허용
                         allowed_codes={811}, fallback=FB())
    rec = pr.recommend_for(_row(3))
    assert rec.account_code == 999       # 폴백


def test_pick_missing_row_unresolved_without_fallback():
    pr = PickRecommender({1: {"account_code": 811}}, allowed_codes={811})
    assert pr.recommend_for(_row(99)).resolved is False


# ── analyze / convert ──

def _file_with_unrecommended(tmp_path):
    return _xlsx(tmp_path / "m.xlsx", [
        # 분류된 다른 거래처(시드용, 미추천과 매칭 안 됨)
        ["2026", "03-10", "A", "다른가게", "법인", "비품", 1000, 100, 0, 1100,
         "공제", "도매", "문구", "카과", "(판)복리후생비", "", "", "", "111-11-11119"],
        # 미추천 — 시드로 못 푸는 단독 행 → 호스트 추정 대상
        ["2026", "03-15", "A", "고유카페XYZ", "법인", "커피", 5000, 500, 0, 5500,
         "공제", "음식점", "카페", "카과", "", "", "", "미추천", "222-22-22229"],
    ])


def test_analyze_returns_features_without_pii(tmp_path):
    res = analyze(_file_with_unrecommended(tmp_path))
    assert res["ok"] is True
    assert len(res["needs_account"]) == 1
    item = res["needs_account"][0]
    assert set(item) >= {"id", "업태", "종목", "품명", "유형"}
    assert item["업태"] == "음식점"
    blob = json.dumps(res, ensure_ascii=False)
    assert "고유카페XYZ" not in blob          # 거래처 실명 미노출
    assert "222-22-22229" not in blob          # 사업자번호 미노출
    assert any(a["code"] == 811 for a in res["allowed_accounts"])


def test_convert_applies_host_recommendation(tmp_path):
    src = _file_with_unrecommended(tmp_path)
    res = analyze(src)
    rid = res["needs_account"][0]["id"]
    out = convert(src, tmp_path / "out", recommendations=[
        {"id": rid, "account_code": 811, "confidence": 0.95, "rationale": "카페 커피=복리후생비"},
    ])
    assert out["ok"] is True
    # 출력 .xls 에서 해당 행의 차변계정코드 811 확인
    xls = tmp_path / "out" / "m_upload.xls"
    sh = xlrd.open_workbook(xls).sheet_by_index(0)
    codes = [sh.cell_value(r, sh.row_values(0).index("차변계정코드"))
             for r in range(1, sh.nrows)]
    assert 811.0 in codes
    # 추천 내역에 AI 추정 근거가 마스킹되어 노출
    assert any("AI" in ln or "복리후생비" in ln or "811" in ln
               for ln in out["files"][0]["recommendations"])
