"""대행사 건의 원본 결제내역 되찾기.

카드 매입 내역에 실제 가맹점 대신 결제대행사가 찍힌 건(전체의 23%)은 그 이름만으로는
무엇을 샀는지 알 수 없다. 원본은 **카드사 이용내역**에 있으므로, 거기서 받아와 붙인다.

승인번호는 위하고 다운로드본에 없다(19개 컬럼에 카드번호·승인번호 모두 없음).
그래서 승인번호는 **주는 키가 아니라 받아오는 값**이고, 대조는
`수임처 + 날짜 + 금액` 으로 한다 — 실측 유일성 99.7%(1,797/1,803).
"""
from kafa.lookup.merge import Resolved, merge_statements
from kafa.lookup.request import LookupPlan, build_plan
from kafa.lookup.statements import StatementRow, read_statement

__all__ = ["LookupPlan", "build_plan", "StatementRow", "read_statement",
           "Resolved", "merge_statements"]
