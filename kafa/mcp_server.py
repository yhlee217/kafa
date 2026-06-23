"""kafa MCP 서버 — Claude Desktop / Claude Code 용.

설계 의도(사용자 요구):
  - "쉽게 쓰는 형태": 자연어로 "이 파일 변환해줘" 하면 로컬에서 처리.
  - "정해진 템플릿으로 항상 오차없이": 변환은 결정론적 코드(룰 엔진)가 수행, 출력은 고정
    .xls 스키마. 호스트 모델의 추정 결과는 결정 캐시로 같은 입력→같은 결과.
  - "별도 API 키로 추가 토큰을 쓰지 않음": 미추천 행의 차변계정 추정은 **이미 켜진
    호스트 Claude(Desktop/Code)**가 수행한다(별도 ANTHROPIC_API_KEY 불필요). kafa 는
    비-PII 특징만 모델에 넘기고(analyze), 모델이 정한 계정을 받아(convert) .xls 를 만든다.

권장 흐름(호스트 Claude 가 수행):
  1) analyze(파일) → 미추천 행의 비-PII 특징(업태/종목/품명/유형) 확인
  2) (있으면) 각 항목의 차변계정을 추정
  3) convert(파일, 출력폴더, recommendations=[...]) → 업로드용 .xls 생성
  미추천 행이 없으면 convert 만 한 번 호출하면 된다.

보안 제0원칙: 모든 도구는 **마스킹 요약/비-PII 특징만** 반환·수신한다. 거래처 실명·
사업자번호는 절대 모델에 전달되지 않는다. 실제 .xls/CSV 는 로컬 디스크에만.

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
    def preview(input_path: str) -> dict:
        """변환 전 점검: 행수·전표상태 분포(미추천/중복)·유형 분포·마스킹된 거래처 샘플.

        위하고 다운로드본이 아니면 ok=False 와 누락 컬럼 안내를 반환한다. raw PII 미반환.
        """
        return service.preview(input_path)

    @mcp.tool()
    def analyze(input_path: str) -> dict:
        """변환 전 분석 — 결정론으로 풀리지 않는 '미추천' 행의 **비-PII 특징**만 돌려준다.

        needs_account 의 각 항목(업태/종목/품명/유형)에 대해 당신(호스트 Claude)이
        차변계정을 추정하고, allowed_accounts 의 code 중 하나를 골라
        convert(recommendations=[{id, account_code, confidence, rationale}]) 로 넘기세요.
        거래처/사업자번호 같은 식별정보는 제공되지 않습니다(보안). needs_account 가 비어 있으면
        바로 convert 만 호출하면 됩니다.
        """
        return service.analyze(input_path)

    @mcp.tool()
    def convert(
        input_path: str,
        output_dir: str,
        client_type: str = "corporate",
        recommendations: Optional[list] = None,
        truth: Optional[str] = None,
        client_report: Optional[bool] = None,
        recon_store: Optional[str] = None,
    ) -> dict:
        """위하고 신용카드 다운로드본(.xlsx)을 업로드용 .xls 로 변환한다.

        결과 .xls/CSV 는 output_dir 에 로컬 저장되고, 이 함수는 마스킹 요약만 반환한다.
        함께 생성되는 보조 산출물(요약은 마스킹/집계만): 검토(_review)·부가세(_vat)·증빙리스크
        (_risk)·신고전점검(_prefile)·사업자번호검증(_bizno). recon_store 지정 시 전월 대비
        거래처/계정 대사(_recon)도 생성.
        recommendations: analyze 로 받은 미추천 행의 차변계정 추정 결과(선택).
          [{"id": <analyze의 id>, "account_code": <allowed code>, "confidence": 0~1, "rationale": "근거"}].
          미지정/미허용 항목은 자동 시드 추천으로 폴백한다.
        client_type: 'corporate'(법인, 기본) | 'individual'(개인-보류, 법인 폴백).
        truth: 담당자 정답 CSV 경로(선택) — 정확도 검증.
        client_report: True 면 고객 제공용 요약(_client.txt)도 생성(선택, 기본 off).
          사용자가 '고객 보고서/고객용 요약도 만들어줘'라고 할 때만 True 로 넘기세요.
        recon_store: 전월 대비 거래처/계정 대사 기준선 JSON 경로(선택). 매달 같은 경로를
          주면 신규 거래처·계정 변동을 누적 점검한다.
        """
        return service.convert(input_path, output_dir, client_type=client_type,
                               recommendations=recommendations, truth=truth,
                               client_report=client_report, recon_store=recon_store)

    @mcp.tool()
    def withholding(payments: list) -> dict:
        """원천징수세액(소득세+지방소득세)을 유형별로 계산한다(세무대리인 보조).

        payments: [{"amount": 1000000, "type": "사업소득"}, ...] (또는 [[금액, 유형], ...]).
          type 은 '사업소득'(3.3%) / '기타소득'(8.8%) 등. **금액·유형만** 넘기세요 —
          수령자 성명·주민번호 같은 PII 는 넣지 마세요(계산에 불필요).
        반환: 건별 소득세/지방소득세/실지급액 + 합계 + 표 텍스트.
        """
        return service.withholding_calc(payments)

    @mcp.tool()
    def intake(period: str, received_types: list) -> dict:
        """월별 자료 수취 체크리스트와 누락 자료 요청 메시지 초안을 만든다(세무대리인 보조).

        period: 예 '2026-03'. received_types: 이미 받은 자료유형 목록
          (예: ['신용카드 매입내역', '통장 거래내역']).
        고객명은 넣지 마세요 — 메시지엔 '{고객명}' 자리표시자가 들어가니 담당자가 채우세요.
        """
        return service.intake_checklist(period, received_types)

    return mcp


def main() -> None:
    _build_server().run()


if __name__ == "__main__":
    main()
