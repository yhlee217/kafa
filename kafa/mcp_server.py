"""kafa MCP 서버 — Claude Desktop 등 MCP 클라이언트용.

설계 의도(사용자 요구):
  - "사용하기 쉬운 형태": Claude Desktop 에서 자연어로 변환을 요청하면 로컬에서 실행.
  - "정해진 템플릿으로 항상 오차없이": 데이터 변환은 결정론적 코드(룰 엔진)가 수행하고,
    출력은 고정 .xls 스키마로만 생성된다. LLM 은 raw 데이터를 만지지 않는다.

보안 제0원칙: 모든 도구는 **마스킹된 요약만** 반환한다. 사업자번호·거래처 실명 등
raw PII 는 절대 도구 결과(=LLM 컨텍스트)에 싣지 않는다. 실제 .xls/CSV 는 로컬 디스크에만.

실행:  python -m kafa.mcp_server   (또는 콘솔 스크립트 `kafa-mcp`)
필요:  pip install "kafa[mcp]"     (mcp SDK)
"""
from __future__ import annotations

from typing import Optional

from kafa import service


def _build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - 환경 의존
        raise SystemExit(
            "MCP SDK가 없습니다. 설치: pip install \"kafa[mcp]\"  (또는 pip install mcp)"
        ) from e

    mcp = FastMCP("kafa")

    @mcp.tool()
    def convert(
        input_path: str,
        output_dir: str,
        client_type: str = "corporate",
        truth: Optional[str] = None,
    ) -> dict:
        """위하고 신용카드 다운로드본(.xlsx 파일 또는 폴더)을 업로드용 .xls 로 변환한다.

        결과 .xls/CSV 는 output_dir 에 로컬 저장되고, 이 함수는 마스킹된 요약만 반환한다.
        client_type: 'corporate'(법인, 기본) | 'individual'(개인-보류, 법인 폴백).
        truth: 담당자 정답 CSV 경로(선택) — 정확도 검증.
        """
        return service.convert(input_path, output_dir,
                               client_type=client_type, truth=truth)

    @mcp.tool()
    def preview(input_path: str) -> dict:
        """변환 전 점검: 행수·전표상태 분포(미추천/중복)·유형 분포·마스킹된 거래처 샘플.

        위하고 다운로드본이 아니면 ok=False 와 누락 컬럼 안내를 반환한다.
        raw PII 는 반환하지 않는다.
        """
        return service.preview(input_path)

    return mcp


def main() -> None:
    _build_server().run()


if __name__ == "__main__":
    main()
