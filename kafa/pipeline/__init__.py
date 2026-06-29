"""베이스 데이터 파이프라인 — inbox 디렉토리 일괄 처리.

사람이 위하고 다운로드본을 inbox 에 모아두면(ToS 안전), 이 파이프라인이 고객별로
분류·미추천해소·리포트를 만들고 SQLite 에 누적 적재한 뒤 업로드용 .xls 를 산출한다.
위하고 접근은 일절 하지 않는다(로컬 파일만 처리). 설계: docs/pipeline_plan.md.
"""
from kafa.pipeline.runner import (
    FileOutcome,
    PipelineResult,
    resolve_client,
    run_pipeline,
)

__all__ = ["run_pipeline", "PipelineResult", "FileOutcome", "resolve_client"]
