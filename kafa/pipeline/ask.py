"""처리 전에 Claude 에게 물을 것을 정리한다 — 질문지(<결과폴더>/_ai/questions.json).

inbox 의 파일을 **처리와 똑같이 준비해서**(이력·시드·기존 답·유형 추천까지) 분류만 해 보고,
그래도 계정이 안 정해지는 행의 비-PII 특징만 모은다. 파일은 옮기지 않고 업로드본도 쓰지
않는다(중복 가드도 기록하지 않는다 — flush 하지 않으므로).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PrepareResult:
    files: int = 0
    questions: object = None             # host_picks.QuestionSet
    path: Path | None = None          # 질문지
    merged: object = None                # host_picks.MergeResult(새 답지를 먼저 합쳤으면)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def new(self) -> int:
        return len(self.questions.questions) if self.questions is not None else 0


def prepare_questions(inbox, output_dir, *, client_type: str | None = None,
                      config_dir: str | None = None) -> PrepareResult:
    from kafa.cli import classify_rows
    from kafa.pipeline.runner import file_context, list_inbox, load_office_prior
    from kafa.recommend.host_picks import (
        QuestionSet,
        account_catalog,
        load_picks,
        merge_answers,
        write_questions,
    )
    from kafa.store.db import VoucherStore

    inbox = Path(inbox)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = PrepareResult(merged=merge_answers(out_dir, config_dir=config_dir))
    picks = load_picks(out_dir)
    qs = QuestionSet()
    files = list_inbox(inbox, out_dir)
    res.files = len(files)
    db = VoucherStore(out_dir / "kafa.db")
    try:
        prior = load_office_prior(db, config_dir=config_dir)
        for f in files:
            try:
                ctx = file_context(f, inbox, out_dir, db, picks, prior=prior,
                                   client_type=client_type, config_dir=config_dir)
                classified, _ = classify_rows(
                    ctx.rows, client_type=ctx.ctype, seed=ctx.seed,
                    recommender=ctx.recommender, dup=ctx.dup, profile=ctx.profile,
                    type_index=ctx.type_index, config_dir=config_dir)
                qs.add(classified, picks, config_dir=config_dir)
            except Exception as e:  # noqa: BLE001 — 파일별 격리(처리 단계에서 다시 걸린다)
                res.failures[str(f.relative_to(inbox))] = f"{type(e).__name__}: {e}"
        usage = db.account_usage()
    finally:
        db.close()
    res.questions = qs
    res.path = write_questions(out_dir, qs.questions,
                               account_catalog(usage, config_dir=config_dir))
    return res
