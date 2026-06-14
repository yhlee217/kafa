"""서비스 파사드 — 배치 오케스트레이션 + LLM 안전(마스킹) 요약.

이 모듈이 '사용자 입장의 진입점'이다. CLI와 MCP 서버가 공통으로 쓴다.
  - run_batch(...)   : 전체 배치 실행 → 내부 리치 결과(BatchResult). 로컬 전용.
  - convert(...)     : run_batch 위에 **마스킹된 JSON 요약**만 반환. LLM/MCP 노출 안전.
  - preview(...)     : 원천 데이터 없이 스키마·분포·마스킹 샘플만. 디스커버리용.

결정론 보장: 데이터 변환은 100% 코드(룰 엔진)가 한다. LLM은 raw 데이터를 만지지 않으며
출력 .xls 는 고정 스키마(io_wehago.schema.OUTPUT_COLUMNS)로만 생성된다.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kafa.config_loader import load_rules
from kafa.dup_guard import DupGuard
from kafa.recommend.seed import SeedIndex, build_seed_from_inputrows
from kafa.security import mask_name


@dataclass
class FileResult:
    input_name: str
    written: int = 0
    skipped: int = 0
    automation_rate: float = 0.0
    output_files: list[str] = field(default_factory=list)
    review_path: str = ""
    csv_path: str = ""
    accuracy: Optional[float] = None
    accuracy_path: str = ""
    accuracy_text: str = ""
    report_obj: object = None       # ReviewReport (로컬 전용 — 마스킹된 라인 보유)
    vat_obj: object = None          # VatSummary (부가세 신고 보조 집계)
    vat_path: str = ""
    client_path: str = ""           # 고객 제공용 요약 리포트(로컬)


@dataclass
class BatchResult:
    files: list[FileResult] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    manifest_path: str = ""

    @property
    def ok(self) -> bool:
        return not self.failures


def _glob_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*.xlsx"))


def run_batch(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    client_type: Optional[str] = None,
    dup_store: Optional[str] = None,
    truth: Optional[str] = None,
    config_dir: Optional[str] = None,
) -> BatchResult:
    """입력(파일/폴더) → 출력 폴더. 파일별 에러 격리, 매니페스트 기록."""
    from kafa.cli import process_rows           # 지연 import(순환 방지)
    from kafa.io_wehago.reader import read_download_xlsx

    load_rules(config_dir)                       # 설정 유효성 조기 검증
    in_path = Path(input_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = _glob_inputs(in_path)
    result = BatchResult()
    if not files:
        result.failures["(입력없음)"] = f"입력 .xlsx 없음: {in_path}"
        return result

    # 1차: 파일별 읽기(에러 격리) → 정상 파일에서 자가 시드 구축
    per_file: dict[Path, list] = {}
    for f in files:
        try:
            per_file[f] = read_download_xlsx(f)
        except Exception as e:                    # noqa: BLE001 — 배치 회복력
            result.failures[f.name] = f"{type(e).__name__}: {e}"

    seed = _merge_seed(per_file.values(), config_dir=config_dir)

    truth_idx = None
    if truth:
        from kafa.eval import load_truth_csv
        truth_idx = load_truth_csv(truth)

    # 미추천 추천기: 기본 시드(로컬). recommend.llm.enabled + API 키 시에만 직접 API.
    from kafa.recommend.recommender import build_recommender
    recommender = build_recommender(seed, config_dir=config_dir)

    dup = DupGuard(dup_store) if dup_store else None

    for f, rows in per_file.items():
        out = out_dir / (f.stem + "_upload.xls")
        try:
            fr = _run_one(rows, out, client_type=client_type, seed=seed,
                          recommender=recommender, dup=dup, truth_idx=truth_idx,
                          config_dir=config_dir)
        except Exception as e:                    # noqa: BLE001
            result.failures[f.name] = f"{type(e).__name__}: {e}"
            continue
        fr.input_name = f.name
        result.files.append(fr)

    # 매니페스트
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "files": [{
            "input": fr.input_name, "written": fr.written, "skipped": fr.skipped,
            "automation_rate": fr.automation_rate, "outputs": fr.output_files,
            "review": fr.review_path, "csv": fr.csv_path,
            "accuracy": fr.accuracy, "accuracy_report": fr.accuracy_path,
        } for fr in result.files],
        "failures": result.failures,
    }
    mpath = out_dir / "_manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    result.manifest_path = str(mpath)
    return result


# ── 공통 헬퍼 ────────────────────────────────────────────────────────

def _merge_seed(rows_iter, *, config_dir):
    """여러 파일의 행에서 자가 시드를 합친다."""
    seed = SeedIndex()
    for rows in rows_iter:
        s = build_seed_from_inputrows(rows, config_dir=config_dir)
        for k, c in s.by_vendor.items():
            seed.by_vendor.setdefault(k, Counter()).update(c)
        for k, c in s.by_bizno.items():
            seed.by_bizno.setdefault(k, Counter()).update(c)
    return seed


def _run_one(rows, out, *, client_type, seed, recommender, dup, truth_idx,
             config_dir) -> FileResult:
    """한 파일 처리 → FileResult (정확도 평가 포함)."""
    from kafa.cli import process_rows
    res = process_rows(rows, out, client_type=client_type, seed=seed,
                       recommender=recommender, dup=dup, config_dir=config_dir)
    rep = res["report_obj"]
    fr = FileResult(
        input_name=out.name, written=res["written"], skipped=res["skipped"],
        automation_rate=rep.automation_rate,
        output_files=[p.name for p in res["files"]],
        review_path=res["report_path"].name, csv_path=res["csv_path"].name,
        report_obj=rep, vat_obj=res.get("vat"),
        vat_path=res["vat_path"].name if res.get("vat_path") else "",
        client_path=res["client_path"].name if res.get("client_path") else "",
    )
    if truth_idx is not None:
        from kafa.eval import evaluate, render_eval
        ev = evaluate(res["classified"], truth_idx)
        acc_path = out.with_name(out.stem + "_accuracy.txt")
        acc_path.write_text(render_eval(ev), encoding="utf-8")
        fr.accuracy = ev.overall_accuracy
        fr.accuracy_path = acc_path.name
        fr.accuracy_text = render_eval(ev)
    return fr


def _masked_file(fr: FileResult) -> dict:
    rep = fr.report_obj
    return {
        "input": fr.input_name,
        "output_files": fr.output_files,
        "written": fr.written,
        "skipped": fr.skipped,
        "automation_rate": round(fr.automation_rate, 4),
        "needs_human": getattr(rep, "needs_human", None),
        "unresolved": getattr(rep, "unresolved", None),
        "needs_review": getattr(rep, "needs_review", None),
        "recommended": getattr(rep, "recommended", None),
        "accuracy": fr.accuracy,
        "review_report": fr.review_path,
        "intermediate_csv": fr.csv_path,
        "vat_summary_report": fr.vat_path,
        "client_report": fr.client_path,
        # 부가세 신고 보조 집계(금액은 PII 아님 — 합계만)
        "vat_summary": ({
            "과세공제_공급가액": str(getattr(fr.vat_obj, "과세공제_공급가액", "")),
            "공제_매입세액": str(getattr(fr.vat_obj, "공제_매입세액", "")),
            "불공제_공급가액": str(getattr(fr.vat_obj, "불공제_공급가액", "")),
            "면세_금액": str(getattr(fr.vat_obj, "면세_금액", "")),
            "의제대상_면세매입액": str(getattr(fr.vat_obj, "의제대상_면세매입액", "")),
            "검토_건수": getattr(fr.vat_obj, "검토_건수", None),
        } if fr.vat_obj is not None else None),
        # 아래 라인들은 build_review 가 이미 마스킹한 것(실명/번호 없음)
        "review_items": list(getattr(rep, "review_lines", []) or []),
        "recommendations": list(getattr(rep, "recommendation_lines", []) or []),
        "vat_anomalies": list(getattr(rep, "vat_anomaly_lines", []) or []),
        "suspect_vendors": list(getattr(rep, "suspect_vendor_lines", []) or []),
    }


# ── MCP/호스트 모델용 안전 API (마스킹) ──────────────────────────────────

def analyze(input_path: str | Path, *, config_dir: Optional[str] = None) -> dict:
    """변환 전 분석 — 결정론으로 풀리지 않는 '미추천' 행의 **비-PII 특징**만 반환.

    호스트 Claude(Desktop/Code)가 이 특징(업태/종목/품명/유형)으로 차변계정을 추정해
    convert(recommendations=...) 로 넘기면 된다. 거래처/사업자번호는 포함되지 않는다.
    별도 API 키·추가 토큰 청구 없음(추정은 이미 켜진 호스트 모델이 수행).
    """
    from kafa.cli import classify_rows
    from kafa.io_wehago.reader import InputFormatError, read_download_xlsx
    from kafa.recommend.features import account_features
    from kafa.recommend.llm import allowed_accounts
    from kafa.recommend.recommender import SeedRecommender
    from kafa.rules.models import Verdict

    try:
        rows = read_download_xlsx(input_path)
    except InputFormatError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    seed = _merge_seed([rows], config_dir=config_dir)
    classified, _ = classify_rows(rows, seed=seed,
                                  recommender=SeedRecommender(seed, config_dir=config_dir),
                                  config_dir=config_dir)
    pending = []
    for c in classified:
        if (not c.skipped and c.판정유형 == Verdict.UNRESOLVED
                and c.차변계정코드 is None and c.source is not None):
            feats = account_features(c.source)
            pending.append({"id": c.source.raw_index, **feats})

    allowed = allowed_accounts(config_dir)
    return {
        "ok": True,
        "total": len([c for c in classified if not c.skipped]),
        "needs_account": pending,                 # 비-PII 특징만
        "allowed_accounts": [{"name": n, "code": allowed[n]} for n in allowed],
        "instruction": (
            "needs_account 의 각 항목에 대해 업태/종목/품명/유형으로 차변계정을 추정하고, "
            "allowed_accounts 의 code 중 하나를 골라 convert(recommendations=[{id, account_code, "
            "confidence, rationale}]) 로 넘기세요. 거래처/사업자번호 같은 식별정보는 제공되지 않습니다."
        ),
    }


def convert(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    client_type: Optional[str] = None,
    recommendations: Optional[list] = None,
    dup_store: Optional[str] = None,
    truth: Optional[str] = None,
    config_dir: Optional[str] = None,
) -> dict:
    """업로드용 .xls 생성 → **마스킹된 요약 dict** 반환. raw PII 미포함.

    recommendations 가 주어지면(호스트 Claude 가 analyze 로 추정한 차변계정) 그 값을 적용한다.
    형식: [{"id": <analyze의 id>, "account_code": int, "confidence"?: float, "rationale"?: str}].
    미지정/미허용/미매칭 항목은 자동 시드 추천으로 폴백한다.
    """
    load_rules(config_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if recommendations:
        # 호스트 모델 추정 적용 — 단일 파일 전제(id=raw_index 는 파일별 고유)
        from kafa.cli import process_rows
        from kafa.io_wehago.reader import InputFormatError, read_download_xlsx
        from kafa.recommend.llm import allowed_accounts
        from kafa.recommend.recommender import PickRecommender, SeedRecommender

        in_path = Path(input_path)
        if not in_path.is_file():
            return {"ok": False, "error": "recommendations 적용은 단일 .xlsx 파일에만 가능합니다."}
        try:
            rows = read_download_xlsx(in_path)
        except (InputFormatError, Exception) as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        seed = _merge_seed([rows], config_dir=config_dir)
        picks = {int(r["id"]): r for r in recommendations if r.get("id") is not None}
        allowed_codes = set(allowed_accounts(config_dir).values())
        recommender = PickRecommender(
            picks, allowed_codes,
            fallback=SeedRecommender(seed, config_dir=config_dir))

        out = out_dir / (in_path.stem + "_upload.xls")
        truth_idx = None
        if truth:
            from kafa.eval import load_truth_csv
            truth_idx = load_truth_csv(truth)
        fr = _run_one(rows, out, client_type=client_type, seed=seed,
                      recommender=recommender, dup=None, truth_idx=truth_idx,
                      config_dir=config_dir)
        fr.input_name = in_path.name
        return {"ok": True, "output_dir": str(out_dir), "manifest": None,
                "files": [_masked_file(fr)], "failures": {},
                "guidance": _guidance([_masked_file(fr)], {})}

    batch = run_batch(input_path, output_dir, client_type=client_type,
                      dup_store=dup_store, truth=truth, config_dir=config_dir)
    files = [_masked_file(fr) for fr in batch.files]
    return {
        "ok": batch.ok,
        "output_dir": str(Path(output_dir)),
        "manifest": batch.manifest_path,
        "files": files,
        "failures": batch.failures,
        "guidance": _guidance(files, batch.failures),
    }


def _guidance(files: list[dict], failures: dict) -> str:
    """담당자용 한 줄 안내(한국어). 마스킹된 집계만."""
    if not files and failures:
        return "처리된 파일이 없습니다. 입력이 위하고 다운로드본(.xlsx)인지 확인하세요."
    parts = []
    for fr in files:
        msg = (f"{fr['input']}: 업로드본 {fr['output_files']} 생성, "
               f"자동처리율 {fr['automation_rate']:.0%}")
        if fr["needs_human"]:
            msg += f", 담당자 확인 {fr['needs_human']}건({fr['review_report']} 참고)"
        if fr.get("accuracy") is not None:
            msg += f", 정확도 {fr['accuracy']:.0%}"
        parts.append(msg)
    if failures:
        parts.append(f"실패 {len(failures)}건: {list(failures)}")
    return " / ".join(parts)


def preview(input_path: str | Path) -> dict:
    """원천 데이터 없이 스키마·분포·마스킹 샘플만 반환(디스커버리). 안전.

    위하고 파일이 아니면 ok=False + 누락 컬럼 안내.
    """
    from kafa.io_wehago.reader import InputFormatError, read_download_xlsx

    try:
        rows = read_download_xlsx(input_path)
    except InputFormatError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    status = Counter((r.전표상태 or "정상").strip() or "정상" for r in rows)
    types = Counter((r.유형 or "(미상)").strip() or "(미상)" for r in rows)
    sample = []
    seen = set()
    for r in rows:
        m = mask_name(r.거래처)
        if m and m not in seen:
            seen.add(m)
            sample.append(m)
        if len(sample) >= 5:
            break
    return {
        "ok": True,
        "row_count": len(rows),
        "status_distribution": dict(status),
        "unrecommended": status.get("미추천", 0),
        "duplicate": status.get("중복전표", 0),
        "type_distribution": dict(types),
        "vendor_sample_masked": sample,
    }
