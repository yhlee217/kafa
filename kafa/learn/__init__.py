"""처리 이력에서 규칙을 역으로 추정 — 보류 항목 해소 보조.

위하고 다운로드본에서 **이미 채워진 행**(차변계정·대변계정·유형 보유)은 담당자가
그동안 실제로 처리해온 결과다. 이 이력을 모아 분포를 보면, 지금 보류 중인 항목
(개인사업자 상대계정 · 면세(카면) 처리 · 간이과세 식별 · 봉사료 · 미매핑 계정)을
상당 부분 추정할 수 있다.

원칙:
- **추정일 뿐 자동 적용하지 않는다.** 근거 건수·비율을 함께 제시하고, 반영은 사람이 결정.
- 보안 제0원칙: 리포트에 거래처 실명·사업자번호는 **넣지 않는다**(계정명/업태/종목/유형·건수만).
"""
from kafa.learn.infer import (
    InferenceReport,
    Observation,
    infer_rules,
    propose_config,
    render_inference,
)

__all__ = ["infer_rules", "render_inference", "propose_config",
           "InferenceReport", "Observation"]
