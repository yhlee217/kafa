"""베이스 데이터 로컬 저장소(SQLite).

고객·기간별 거래(전표)를 누적하는 단일 원본(source of truth). 분류·추천·리포트·대사가
모두 여기서 읽어간다. 보안 제0원칙: 이 DB 는 **사용자 PC 로컬에만** 존재하며, 내용은
어떤 LLM/외부로도 전송하지 않는다(PII 평문 보관은 로컬 코드 처리 범위 내에서만).
"""
from kafa.store.db import IngestResult, VoucherStore

__all__ = ["VoucherStore", "IngestResult"]
