"""inbox 디렉토리 일괄 처리 오케스트레이션.

흐름(파일별, 에러 격리):
  read → (고객/기간 결정) → process_rows(분류·추천·리포트·업로드 .xls, 고객별 dup/recon)
       → VoucherStore 누적 적재 → 원본을 _archive 로 이동.
끝에 실행/실패를 _logs/manifest.json 으로 남긴다.

보안 제0원칙: 위하고 접근 없음(로컬 파일만). DB·산출물은 로컬. 외부 노출은 리포트의
마스킹된 요약뿐. 고객별 dup·recon 기준선을 분리 보존해 대사 정확성을 지킨다.
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FileOutcome:
    file: str
    client: str
    period: str
    written: int = 0
    skipped: int = 0
    inserted: int = 0
    existing: int = 0


@dataclass
class PipelineResult:
    outcomes: list[FileOutcome] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    db_path: str = ""
    total_in_db: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


def resolve_client(inbox: Path, file: Path) -> str:
    """고객 ID 결정 — inbox 바로 아래 하위폴더명. 최상위 파일이면 파일 stem."""
    rel = file.relative_to(inbox)
    return rel.parts[0] if len(rel.parts) > 1 else file.stem


def _period_of(rows) -> str:
    """가장 흔한 연도-월을 기간으로. 없으면 '기타'."""
    cnt: Counter = Counter()
    for r in rows:
        y = (r.연도 or "").strip()
        d = (r.일자 or "").strip()
        mm = d.split("-")[0] if "-" in d else (d[:2] if d[:2].isdigit() else "")
        if y and mm:
            cnt[f"{y}-{mm}"] += 1
        elif y:
            cnt[y] += 1
    return cnt.most_common(1)[0][0] if cnt else "기타"


def _merge_seed(base, extra) -> None:
    """extra 의 빈도를 base 에 합친다(누적 이력 + 이번 배치)."""
    from collections import Counter
    for k, c in extra.by_vendor.items():
        base.by_vendor.setdefault(k, Counter()).update(c)
    for k, c in extra.by_bizno.items():
        base.by_bizno.setdefault(k, Counter()).update(c)


def _uniq(dest: Path) -> Path:
    i = 2
    while True:
        cand = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not cand.exists():
            return cand
        i += 1


def run_pipeline(inbox, output_dir, *, client_type: Optional[str] = None,
                 config_dir: Optional[str] = None,
                 run_id: str = "run") -> PipelineResult:
    """inbox 의 .xlsx 들을 고객별로 일괄 처리 → DB 누적 + 업로드 산출물."""
    from kafa.agent.recon import VendorBaseline
    from kafa.cli import process_rows
    from kafa.dup_guard import DupGuard
    from kafa.io_wehago.reader import read_download_xlsx
    from kafa.recommend.recommender import build_recommender
    from kafa.recommend.seed import build_seed_from_inputrows, build_seed_index
    from kafa.store.db import VoucherStore

    inbox = Path(inbox)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "_archive"

    files = sorted(p for p in inbox.rglob("*.xlsx")
                   if not p.name.startswith("~$") and archive not in p.parents)

    result = PipelineResult(db_path=str(out_dir / "kafa.db"))
    db = VoucherStore(out_dir / "kafa.db")
    try:
        for f in files:
            client = resolve_client(inbox, f)
            try:
                rows = read_download_xlsx(f)
                period = _period_of(rows)
                state = out_dir / client / "_state"
                dup = DupGuard(state / "dup.json")
                recon = VendorBaseline(state / "recon.json")
                # 자가 시딩: 이번 파일 + **이 고객의 누적 이력(DB)**.
                # 지난 달에 처리한 가맹점이 이번 달에도 나오면 그대로 해소된다.
                seed = build_seed_from_inputrows(rows, config_dir=config_dir)
                _merge_seed(seed, build_seed_index(
                    db.seed_records(client, exclude_source=f.name)))
                recommender = build_recommender(seed, config_dir=config_dir)

                outp = out_dir / client / period / (f.stem + "_upload.xls")
                outp.parent.mkdir(parents=True, exist_ok=True)
                res = process_rows(rows, outp, client_type=client_type, seed=seed,
                                   recommender=recommender, dup=dup, recon=recon,
                                   config_dir=config_dir)

                db.upsert_client(client)
                ing = db.upsert_vouchers(client, period, res["classified"],
                                         source_file=f.name)

                adir = archive / client
                adir.mkdir(parents=True, exist_ok=True)
                dest = adir / f.name
                shutil.move(str(f), str(dest if not dest.exists() else _uniq(dest)))

                result.outcomes.append(FileOutcome(
                    file=f.name, client=client, period=period,
                    written=res["written"], skipped=res["skipped"],
                    inserted=ing.inserted, existing=ing.existing))
            except Exception as e:  # noqa: BLE001 — 파일별 에러 격리
                result.failures[str(f.relative_to(inbox))] = f"{type(e).__name__}: {e}"

        db.record_run(run_id, inbox, len(files),
                      sum(o.written for o in result.outcomes),
                      sum(o.skipped for o in result.outcomes), len(result.failures))
        result.total_in_db = db.count()
    finally:
        db.close()

    logs = out_dir / "_logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "manifest.json").write_text(
        json.dumps({"outcomes": [asdict(o) for o in result.outcomes],
                    "failures": result.failures,
                    "total_in_db": result.total_in_db},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    try:  # 고객 진행 현황 보드 갱신(실패해도 파이프라인 결과엔 영향 없음)
        from kafa.pipeline.summary import write_board
        write_board(out_dir)
    except Exception:  # noqa: BLE001
        pass
    return result
