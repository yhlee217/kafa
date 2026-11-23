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
    upgraded: int = 0
    existing: int = 0
    integrity: str = ""     # 행 수·금액 대사 한 줄(kafa/integrity.py)
    ai_applied: int = 0     # Claude 답(_ai/picks.json)으로 계정을 채운 행


@dataclass
class PipelineResult:
    outcomes: list[FileOutcome] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    db_path: str = ""
    total_in_db: int = 0
    ai_note: str = ""       # Claude 답지 반영 한 줄(있을 때만)

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


def list_inbox(inbox: Path, out_dir: Path) -> list[Path]:
    """처리할 다운로드본 — 임시파일(~$)·보관함(_archive) 제외."""
    archive = Path(out_dir) / "_archive"
    return sorted(p for p in Path(inbox).rglob("*.xlsx")
                  if not p.name.startswith("~$") and archive not in p.parents)


@dataclass
class FileContext:
    """파일 하나를 분류하는 데 필요한 것 — 처리(run_pipeline)와 질문지(ask)가 같이 쓴다.

    둘이 다르게 준비하면 질문지에 '처리하면 풀릴 행'이 섞이거나 빠진다.
    """
    client: str
    rows: list
    period: str
    dup: object
    seed: object
    recommender: object
    type_index: object
    profile: dict
    ctype: Optional[str]


def file_context(f: Path, inbox: Path, out_dir: Path, db, picks: dict, *,
                 prior=None, client_type: Optional[str] = None,
                 config_dir: Optional[str] = None) -> FileContext:
    from kafa.config_loader import client_profile
    from kafa.dup_guard import DupGuard
    from kafa.io_wehago.reader import read_download_xlsx
    from kafa.recommend import office_prior
    from kafa.recommend.host_picks import wrap
    from kafa.recommend.recommender import build_recommender
    from kafa.recommend.seed import build_seed_from_inputrows, build_seed_index
    from kafa.recommend.type_seed import build_type_index, build_type_index_from_inputrows

    client = resolve_client(inbox, f)
    rows = read_download_xlsx(f)
    dup = DupGuard(out_dir / client / "_state" / "dup.json")
    # 자가 시딩: 이번 파일 + **이 고객의 누적 이력(DB)**.
    # 지난 달에 처리한 가맹점이 이번 달에도 나오면 그대로 해소된다.
    seed = build_seed_from_inputrows(rows, config_dir=config_dir)
    _merge_seed(seed, build_seed_index(db.seed_records(client, exclude_source=f.name)))
    # 수임처 이력·시드 → 사무실 전체 이력(켜져 있으면) → Claude 답(_ai/picks.json) 순서
    recommender = office_prior.wrap(build_recommender(seed, config_dir=config_dir), prior,
                                    config_dir=config_dir)
    recommender = wrap(recommender, picks, config_dir=config_dir)
    # 유형 추천 재료: 이번 파일의 처리된 행 + 이 수임처의 지난 처리 이력(DB)
    type_index = build_type_index_from_inputrows(rows).merge(build_type_index(
        db.type_records(client, exclude_source=f.name, config_dir=config_dir)))
    # 수임처 속성(개인/법인·직원 유무) — 고객마다 다르므로 배치 단일값이 아니라
    # clients.yaml 에서 읽는다. 명령행 --client-type 이 있으면 그것이 우선.
    profile = client_profile(client, config_dir)
    return FileContext(client=client, rows=rows, period=_period_of(rows), dup=dup,
                       seed=seed, recommender=recommender, type_index=type_index,
                       profile=profile, ctype=client_type or profile.get("client_type"))


def load_office_prior(db, *, config_dir: Optional[str] = None):
    """사무실 전체 이력(켜져 있을 때만 DB 에서 읽는다). 실행 한 번에 한 번 만든다."""
    from kafa.recommend.office_prior import build_office_prior, settings
    if not settings(config_dir)["enabled"]:
        return None
    return build_office_prior([(c, v, u, j, code) for c, v, _b, u, j, code
                               in db.prior_records()], config_dir=config_dir)


def run_pipeline(inbox, output_dir, *, client_type: Optional[str] = None,
                 config_dir: Optional[str] = None,
                 run_id: str = "run") -> PipelineResult:
    """inbox 의 .xlsx 들을 고객별로 일괄 처리 → DB 누적 + 업로드 산출물."""
    from kafa.agent.recon import VendorBaseline
    from kafa.cli import process_rows
    from kafa.integrity import IntegrityError, check_store, hold
    from kafa.integrity import render as render_tally
    from kafa.recommend.host_picks import BASIS_PREFIX, load_picks, merge_answers, settings
    from kafa.store.db import VoucherStore

    inbox = Path(inbox)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "_archive"

    files = list_inbox(inbox, out_dir)

    result = PipelineResult(db_path=str(out_dir / "kafa.db"))
    picks: dict = {}
    if settings(config_dir)["enabled"]:
        merged = merge_answers(out_dir, config_dir=config_dir)   # 새 답지가 있으면 먼저 합친다
        if merged is not None:
            result.ai_note = merged.line()
        picks = load_picks(out_dir)
    db = VoucherStore(out_dir / "kafa.db")
    try:
        prior = load_office_prior(db, config_dir=config_dir)
        for f in files:
            client = resolve_client(inbox, f)
            try:
                ctx = file_context(f, inbox, out_dir, db, picks, prior=prior,
                                   client_type=client_type, config_dir=config_dir)
                rows, period, dup = ctx.rows, ctx.period, ctx.dup
                recon = VendorBaseline(out_dir / client / "_state" / "recon.json")

                outp = out_dir / client / period / (f.stem + "_upload.xls")
                outp.parent.mkdir(parents=True, exist_ok=True)
                res = process_rows(rows, outp, client_type=ctx.ctype, seed=ctx.seed,
                                   recommender=ctx.recommender, dup=dup, recon=recon,
                                   profile=ctx.profile, flush_dup=False,
                                   type_index=ctx.type_index, config_dir=config_dir)

                db.upsert_client(client)
                ing = db.upsert_vouchers(client, period, res["classified"],
                                         source_file=f.name)

                # DB 적재 대사 — 키가 겹치거나 빠지면 그만큼 전표가 사라진 것이다.
                # 원본을 보관함으로 옮기지 않으므로 고친 뒤 다시 돌리면 된다.
                tally = res["tally"]
                check_store(tally, ing.keys, db.count_keys(ing.keys))
                if not tally.ok:
                    hold(res["files"])
                    res["tally_path"].write_text(render_tally(tally, held=True),
                                                 encoding="utf-8")
                    raise IntegrityError(tally, res["tally_path"])
                dup.flush()          # 끝까지 맞은 파일만 '처리함'으로 기록한다

                adir = archive / client
                adir.mkdir(parents=True, exist_ok=True)
                dest = adir / f.name
                shutil.move(str(f), str(dest if not dest.exists() else _uniq(dest)))

                result.outcomes.append(FileOutcome(
                    file=f.name, client=client, period=period,
                    written=res["written"], skipped=res["skipped"],
                    inserted=ing.inserted, existing=ing.existing,
                    upgraded=ing.upgraded, integrity=tally.line(),
                    ai_applied=sum(1 for c in res["classified"]
                                   if not c.skipped
                                   and (c.추천근거 or "").startswith(BASIS_PREFIX))))
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
                    "ai_note": result.ai_note,
                    "total_in_db": result.total_in_db},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    try:  # 고객 진행 현황 보드 갱신(실패해도 파이프라인 결과엔 영향 없음)
        from kafa.pipeline.summary import write_board
        write_board(out_dir)
    except Exception:  # noqa: BLE001
        pass
    return result
