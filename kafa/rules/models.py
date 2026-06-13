"""룰 엔진 데이터 모델.

판정유형(Verdict):
  - rule_confirmed : 확정 규칙으로 결정. 근거 = 규칙ID. 신뢰도 개념 없음(=1.0 표기).
  - recommended    : 미추천 해소 등 추천(Phase 2). 신뢰도 0~1 의미 있음.
  - unresolved     : 결정 불가. 담당자 확인 필요.
  - review         : 자동 확정 금지, 담당자 확인 플래그(예: 비영업용 승용차 의심).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    RULE_CONFIRMED = "rule_confirmed"
    RECOMMENDED = "recommended"
    UNRESOLVED = "unresolved"
    REVIEW = "review"


class Deduct(str, Enum):
    """공제여부 3단."""
    DEDUCTIBLE = "공제"
    NON_DEDUCTIBLE = "불공제"
    REVIEW = "검토"   # 담당자 확인 필요 (자동 확정 금지)


@dataclass
class RuleResult:
    """단일 룰 함수의 산출. value + 판정유형 + 근거 규칙ID + (선택)신뢰도."""
    value: object
    verdict: Verdict
    rule_id: str
    confidence: Optional[float] = None   # recommended 에만 의미
    note: str = ""


@dataclass
class InputRow:
    """다운로드본(.xlsx) 1행을 정규화한 입력. 양식엔 없지만 판정에 쓰는 컬럼 포함.

    금액은 Decimal 로 보관(부동소수 오차 방지).
    """
    연도: str = ""
    일자: str = ""
    code: str = ""
    거래처: str = ""
    구분: str = ""          # 가맹점 법인격(법인/일반) — 고객사 아님(1.4)
    품명: str = ""
    공급가액: Decimal = Decimal(0)
    세액: Decimal = Decimal(0)
    비과세: Decimal = Decimal(0)
    합계: Decimal = Decimal(0)
    국세청: str = ""        # 공제/불공제 자동추정값(참고)
    업태: str = ""          # 가맹점 업종(1.5)
    종목: str = ""
    유형: str = ""          # 위하고 1차 분류 라벨(있을 수도, 미추천일 수도)
    차변계정: str = ""      # 계정명(한글). 미추천이면 비어있음.
    대변계정: str = ""
    관리: str = ""
    전표상태: str = ""      # 미추천 / 중복전표 / ...
    사업자등록번호: str = ""

    # 원본 추적용(마스킹 전 식별). 디버그/리포트에서만.
    raw_index: Optional[int] = None


@dataclass
class ClassifiedRow:
    """룰 엔진 출력 1행 (spec Phase 1 컬럼)."""
    유형코드: Optional[int] = None
    차변계정코드: Optional[int] = None
    대변계정코드: Optional[int] = None
    대변거래처: str = ""              # 카드사 (양식엔 없을 수 있음 — 배치 귀속)
    공제여부: Optional[Deduct] = None
    의제대상여부: bool = False
    면세매입액: Decimal = Decimal(0)
    판정근거: list[str] = field(default_factory=list)   # 규칙ID 목록
    판정유형: Verdict = Verdict.RULE_CONFIRMED
    신뢰도: Optional[float] = None

    skipped: bool = False             # 중복전표 등 스킵
    skip_reason: str = ""
    is_reversal: bool = False         # 환불/취소(음수)로 방향 반전 대상
    reversal_applied: bool = False    # 실제 차·대변 swap 적용 완료 여부(중복 방지)
    needs_review: bool = False        # review 플래그(담당자 확인)
    review_reasons: list[str] = field(default_factory=list)

    # 추적용 원본 입력(리포트에서만 사용; LLM 컨텍스트로 보내지 않음)
    source: Optional[InputRow] = None

    def add_rule(self, rule_id: str) -> None:
        if rule_id and rule_id not in self.판정근거:
            self.판정근거.append(rule_id)
