# 결정 기록 (Decision Log) — kafa

표시 규칙: ✅ 확정(화면/양식 검증) · 🤖 AI 잠정판단(검수 전) · ⏸️ 보류(데이터 미확보)

---

## Phase 0 — 입력→출력 필드 매핑표

입력(다운로드본 .xlsx): `연도·일자·Code·거래처·구분·품명·공급가액·세액·비과세·합계·국세청·업태·종목·유형·차변계정·대변계정·관리·전표상태·사업자등록번호`
출력(업로드 .xls): `거래일자·거래처·사업자번호·품명·유형·공급가액·세액·봉사료·합계·차변계정코드·대변계정코드·공제여부·거래구분`

| 출력 컬럼 | 소스 | 변환 | 상태 |
|---|---|---|---|
| 거래일자 (필수) | 연도+일자 | 결합·날짜 정규화 | ✅ / ⏸️ 일자 포맷 |
| 거래처 | 거래처 | 그대로 | ✅ |
| 사업자번호 | 사업자등록번호 | 그대로 | ✅ / ⏸️ 하이픈 포맷 |
| 품명 | 품명 | 그대로 | ✅ |
| 유형 | 유형(라벨) | 라벨→코드(3/57/58/59/63). 불공제는 일반(3) | ✅ |
| 공급가액 | 공급가액 | 그대로 | ✅ |
| 세액 | 세액 | 그대로 | ✅ |
| 봉사료 | 비과세 | 추정 매핑 | 🤖 / ⏸️ 동일성 |
| 합계 (필수) | 합계 | 그대로 | ✅ |
| 차변계정코드 | 차변계정(명) | 명→코드. 미추천이면 Phase2 추천 | ✅ / 핵심 |
| 대변계정코드 | 룰 | 법인=미지급비용 262. 개인=TODO | ✅ |
| 공제여부 | 국세청+룰 | 3단 분기 | ✅ |
| 거래구분 | — | 공란 기본 | 🤖 / ⏸️ 허용값 |

내부 산출(양식엔 없음): 대변거래처(카드사)·의제대상여부·면세매입액·판정근거·판정유형·신뢰도.
파싱 제외: 하단 요약행(카드사별 매입/일반/합계).

### Phase 0 잔여 확인(업로드 시 1회)
1. ⏸️ 봉사료 칸 = 업로드 다이얼로그 비과세 칸 동일성
2. ⏸️ 거래구분 허용값
3. ⏸️ .xlsx 업로드 허용 여부(현재 .xls 고정)
4. ⏸️ **대변거래처(카드사)가 업로드 양식 어디에 들어가나** — 출력 13컬럼에 대변거래처 칸 없음.
   업로드 배치(어느 카드로 받았는지)로 위하고가 자동 귀속하는 구조로 추정 → 확인 필요.
5. ⏸️ 거래일자/사업자번호 포맷, 음수(환불) 표기 방식

---

## 확정 도메인 규칙 (spec §1) → 코드/설정 반영

| 규칙 | 요지 | 반영 위치 |
|---|---|---|
| 1.1 ✅ | 유형코드 3일반/57카과/58카면/59카영/63화물. 불공제 전용코드 없음 → 불공제=일반(3)+국세청 컬럼 | `config/rules.yaml`, `rules/vat_type.py` |
| 1.2 ✅ | 대변=미지급비용(262), 거래처=카드사. 개인=TODO 폴백 | `rules/counterparty.py` |
| 1.3 ✅ | 불공제 3단: 기본=국세청 / 자동불공제 / 의심→검토(자동확정 금지) | `rules/deductibility.py` |
| 1.4 ✅ | 다운로드 '구분'(법인/일반)=가맹점 법인격, 고객사 아님. 상대계정은 클라이언트 기준 | engine `client_type` |
| 1.5 ✅ | 업태/종목=가맹점 업종(불공제 의심 판정용). 의제용 고객사 업종과 다름 | `deductibility.py` |
| 1.6 🤖 | 카면(58)+고객사 음식점 → 의제 후보 플래그+면세매입액만. 율/한도 계산 안 함 | `rules/deemed_credit.py` |
| 1.7 ✅ | 계정명→코드, (제)/(판) 접두 정규화. 시트 파싱 자동생성 예정 | `rules/accounts.py`, `io_wehago/account_sheet.py` |
| 1.8 ✅ | 전표상태=중복전표 스킵+리포트. 2차로 처리키 해시 로컬 보존 | engine, `dup_guard.py` |
| 1.9 ✅ | 음수(환불/취소) 원분류 유지·차대변 반전. 할부 승인시점 1건 | `rules/negative.py` |

### 규칙ID 색인
- VAT-001 라벨매핑 / VAT-002 도출 / VAT-099 미상
- DED-001 자동불공제 / DED-002 국세청따름 / DED-003 검토 / DED-004 기본공제
- ACC-001 정확 / ACC-002 정규화 / ACC-099 미매핑 / ACC-PENDING 미추천
- CP-001 법인 / CP-002 개인폴백 / CP-099 미상
- DEEM-001 의제후보 / DEEM-000 비대상 / DEEM-090 음식점여부미정
- NEG-001 반전 / SKIP-001 중복스킵
- VM-001 사업자번호정확 / VM-002 이름정확 / VM-003 후보 / VM-099 미매칭
- RECO-001 추천채택

### 판정유형(Verdict)
`rule_confirmed`(규칙 확정) · `recommended`(추천, 신뢰도 0~1) ·
`unresolved`(결정불가, 차변 미정 등) · `review`(자동확정 금지, 담당자 확인).

---

## 🤖 AI 잠정판단 (세무 전문가 검수 필요)
- `config/rules.yaml > non_deductible.review_keywords` — 비영업용 승용차/접대성 업종
  키워드 목록. 같은 가맹점도 목적에 따라 갈리므로 **검토 플래그만**(자동 확정 안 함).
- 봉사료=비과세 칸 매핑, 거래구분 공란 기본.

## ⏸️ 보류 항목
1. 개인사업자 상대계정(인출금 등) — 샘플 없음. 법인(262) 폴백 + 검토 플래그.
2. 카면(58) 실제 처리·식당 의제 — 면세 매입 샘플 확보 시 검증.
3. 간이과세자 자동 불공제 — 다운로드에 간이/일반 식별자 유무 확인 후.
4. 계정과목(참고용) 시트 — 미확보. 확보 시 `account_sheet.parse_account_sheet` 구현.
5. Phase 0 잔여 4건(위).

## Phase 2 — 미추천 해소 (핵심: AI 대차변 추정 + 근거 생성)
**결정 변경(사용자 지시 2026-06-13)**: spec v4 의 "Phase 2 외부 LLM 없음"을 사용자가
명시적으로 변경 — **AI(Claude)가 차변계정을 추정하고 근거를 생성**하는 것이 핵심 기능.
- **PII 안전장치(보안 제0원칙 유지)**: LLM 에는 비-PII 특징(`features.account_features`:
  업태/종목/품명/유형)만 전달. 거래처 실명·사업자번호는 **절대 미전송**(features 가 구조적
  차단 + 테스트로 검증). 금액은 계정과 무관하므로 제외(서명 안정화).
- **구조화 출력**: 허용 계정명 enum 강제 → 로컬에서 코드 매핑(항상 유효 코드). confidence 0~1
  클램프, rationale(한국어 근거), alternatives 반환. 모델 기본 `claude-opus-4-8`(config 변경 가능).
- **재현성("항상 오차없이")**: 비-PII 특징 서명 기반 **결정 캐시**. 같은 특징 → 같은 결과
  재사용(중복 호출 제거). `cache_path` 지정 시 런 간 재현. 캐시에 PII 없음.
- **폴백**: API 키 없거나 LLM 실패 시 **자가 시딩**(`build_seed_from_inputrows`) — 같은 배치의
  분류된 행에서 (사업자번호→거래처→유사도) 최빈 계정. 외부 데이터 불필요. 로컬 처리(PII 로컬).
- **근거 생성**(`explain.py`): 모든 행에 규칙ID+맥락 → 한국어 근거. 추천행은 LLM/시드 근거 노출.
  검토 리포트·중간 산출물 CSV(`근거` 컬럼)에 표기.
- API 키 전달: CLI/MCP 서버의 환경변수 `ANTHROPIC_API_KEY`. 미설정 시 자동 시드 폴백.

## Phase 3 — 업로드 .xls 생성
xlwt(CP949). 필수값(거래일자·합계) 검증, CP949 인코딩 안전화(불가문자 치환),
2MB 초과 시 행 단위 자동 분할(`_part01.xls`…). 거래구분은 config 기본 공란([보류]).

## Phase 4 — 검토 리포트 (담당자 전용)
한 화면 요약 + 체크포인트. 섹션: 미해소·검토 / 미추천 자동 추천 내역(근거·신뢰도) /
부가율 이상 거래처 / 홈택스 미등록 의심 거래처 / 스킵.
- **부가율 이상**: 세액>0일 때 세액/공급가액이 표준 10%에서 `report.vat_rate_tol`(기본 0.01)
  초과 편차거나, 공급가액 0인데 세액 존재 → 이상. `validate.vat_rate_anomaly`.
- **홈택스 미등록 의심**: 사업자번호 체크섬(국세청 알고리즘) 무효/미상, 또는 마스터
  미등록 신규(known_biznos 제공 시). 🤖 홈택스 API 없이 로컬 근사. `validate.valid_bizno`.
- **중간 산출물 CSV**(`write_review_csv`): 유형/계정/공제/의제/판정유형/신뢰도/추천근거/
  판정근거 등. 담당자 로컬 검증(수작업 결과 대조)용. 콘솔·외부 요약은 기본 마스킹.

## 서비스 운영 관점 (내부 도구 운영성)
PII 로컬 원칙상 외부 웹서비스화는 의도적으로 배제하고, 세무사무소 내부 운영성에 집중.
- **정확도 검증 하니스**(`eval.py`): 담당자 수작업 정답 CSV(키=사업자번호+합계+품명)와
  대조해 필드별 정확도(차변계정코드·유형코드·공제여부)·자동처리율·불일치 사유별 리포트.
  수용 기준(≥95%) 추적. `--truth` 옵션 → `_accuracy.txt`.
- **자동처리율 KPI**: 리포트 헤더·매니페스트에 노출(자동확정/사람확인/작성).
- **입력 견고성**: 위하고 필수 컬럼(`schema.REQUIRED_INPUT`) 누락 시 `InputFormatError`.
  배치는 파일별 에러 격리 — 실패 파일은 건너뛰고 계속, `_manifest.json`에 사유 기록.
- **배치 매니페스트**(`_manifest.json`): 파일별 작성/스킵/자동처리율/정확도/산출물·실패 사유.
  부분 실패 시 CLI 종료코드 1(자동화/모니터링 신호).

### AI 추정 방식 결정 (사용자 지시 2026-06-13, 2차)
"별도 API 키로 추가 토큰을 소모하지 않기" + "60대 접근성" 요구에 따라:
- **기본 추정 = 호스트 모델**(이미 켜진 Claude Desktop/Code)이 수행. 별도 ANTHROPIC_API_KEY·
  추가 토큰 청구 없음. 흐름: MCP `analyze`(미추천 행의 비-PII 특징 반환) → 호스트 Claude 추정
  → `convert(recommendations=[{id, account_code, confidence, rationale}])` → `PickRecommender`가
  허용 계정만 적용(환각/오류 방어), 미매칭은 자가 시딩 폴백.
- `recommend.llm.enabled` 기본 **false**(API 직접호출 off). 자동 배치에서만 opt-in(추가 비용 고지).
- 접근성: "변환해줘" 한마디 → Claude 가 analyze+convert 자동 수행, 한국어로 결과·다음 행동 안내.
  설명서 docs/usage.md 를 비전문가용으로 단순화. id=raw_index 로 무상태 매칭(PII 불필요).

### 변경 이력 추가
- 2026-06-13: **AI 대차변 추정 + 근거 생성**(사용자 지시로 Phase 2 LLM 전환). recommend/
  {features,llm,explain}.py 추가, Recommender(LLM 우선/시드 폴백)·build_recommender. 비-PII
  특징만 LLM 전달(PII 미전송, 테스트 검증), 결정 캐시로 재현성, 구조화 출력+계정 enum. config
  recommend.llm 섹션(모델/캐시). CSV 에 근거 컬럼. 단위테스트 107케이스.

## 변경 이력
- 2026-06-13: Phase 0 매핑 확정, Phase 1 룰 엔진 + 단위테스트(54) 구현.
  Phase 2/3/4 골격. v3 "법인=미지급금(253)" → v4 "미지급비용(262)"로 정정 반영.
- 2026-06-13: Phase 2 자가 시딩 추천 + Phase 3 2MB 분할·CP949 안전화 구현.
  단위테스트 65케이스. 거래구분 허용값만 [보류].
- 2026-06-13: Phase 4 검토 리포트 구현 — 부가율 이상·미등록 의심(사업자번호 체크섬)·
  추천 내역·중간 산출물 CSV. CLI가 _review.txt/_review.csv 산출. 단위테스트 79케이스.
  이로써 spec v4 Phase 0~4 최소 버전 전부 동작(보류 항목은 데이터 확보 시 확정).
- 2026-06-13: 서비스 운영 기능 — 정확도 검증 하니스(eval.py, --truth), 자동처리율 KPI,
  입력 형식 가드(InputFormatError)·배치 에러 격리, _manifest.json·종료코드. 단위테스트 89케이스.
- 2026-06-13: 사용성 — service.py 파사드(run_batch/convert/preview)로 배치 오케스트레이션
  통합(CLI는 출력기로 경량화), MCP 서버(kafa/mcp_server.py, FastMCP) 추가로 Claude Desktop
  에서 자연어 사용. MCP/LLM 에는 마스킹 요약만 반환(보안 제0원칙). 결정론(동일 입력→동일
  .xls)·preview 안전성 테스트 포함, 단위테스트 94케이스. docs/usage.md(담당자용) 추가.
- 2026-06-13: [세션 내 자율 사이클1] account_sheet 계정과목 시트 파서 구조 기반 구현
  (헤더 자동탐지·'계정코드' 우선·비숫자 행 제외·핸들 재사용). 값 추측 없음(구조만).
  /code-review(high) 자체검수 2건 반영. 단위테스트 119케이스.
- 2026-06-13: [세션 내 자율 사이클2] 시트 자동 머지 연결 — rules.yaml `account_sheet_path`
  지정 시 load_account_codes 가 계정과목 시트를 계정명→코드 매핑에 머지(config 우선,
  파싱 실패 안전). 파서가 실제 매핑에 반영됨. 단위테스트 121케이스.

- 2026-06-14: [방향 확장] **세무대리인 고객 서비스** 관점으로 제품 방향 재정의(`docs/roadmap.md`).
  첫 확장 모듈 `report/vat_summary.py`(부가세 신고 보조 집계 — 과세공제/불공제/면세/의제/검토
  합산, 율·한도는 신고단계 이관). CLI가 `_vat.txt` 산출, service 요약에 비-PII 합계 노출.
  플레이북 우선순위에 '서비스 확장(데이터 불필요)' 추가. 단위테스트 +3.

- 2026-06-14: [서비스 확장 2차] **고객 제공용 요약 리포트**(`report/client_report.py`) —
  세무대리인→고객 비전문가용 한국어 요약(처리현황+부가세관점+고객 확인 요청). 거래처는
  마스킹하고 거래일자·품명·금액으로 식별(안전+실사용). CLI `_client.txt` 산출, service에 경로 노출.
  단위테스트 +4(총 128).

- 2026-06-14: [서비스 확장 3차] **증빙·리스크 점검**(`report/evidence_check.py`) —
  신용카드매출전표=적격증빙 전제로, 공제 제외/확인 대상(불공제·검토·미등록 의심·부가율 이상·
  중복)을 거래별 점검표로. validate.py 재사용. CLI `_risk.txt` 산출, service에 경로·플래그수 노출.
  단위테스트 +3(총 131).

- 2026-06-14: [서비스 확장 4차] 부가세 신고용 **집계표 CSV**(`write_vat_summary_csv`) —
  구분/건수/공급가액/세액. CLI `_vat.csv` 산출. 데이터 불필요한 서비스 확장 4종 완료
  (집계·고객요약·증빙점검·신고CSV). 단위테스트 +1(총 132).

- 2026-06-15: 고객 제공용 요약(`_client.txt`)을 **선택(opt-in)** 으로 변경. 기본 off
  (`config report.client_report`). CLI `--client-report/--no-client-report`, MCP convert
  `client_report` 인자, service/process_rows `client_report` 파라미터로 제어. 나머지 산출물
  (upload/review/vat/risk)은 항상 생성. 단위테스트 +1.

- 2026-06-22: [루프 엔지니어링 세팅] **Actor↔Evaluator 생성-평가-재생성 프레임워크**
  (`kafa/loop/`) 추가. 사용자 제공 포맷(Actor/Evaluator 표준 프롬프트 + 오케스트레이터 제어흐름
  + 튜닝)을 다듬어 구현: ① 프롬프트/루브릭은 `config/loops/*.yaml` 로 외부화(코드 수정 없이
  새 주제), ② temperature 대신 **effort**(현재 Claude(Opus 4.8/Fable 5)는 temperature 미지원),
  Evaluator 는 결정성 위해 effort 낮게 + 구조화 JSON 스키마, ③ 모델 호출은 공급자 비종속
  **주입형 Completion**(테스트는 가짜 콜러블, 실사용 `AnthropicCompletion` opt-in — 추가 토큰),
  ④ 매 회차 결과물/점수/비평을 **JSONL 로그**로 남김(추적성). 제어흐름: critique=None 초기화 →
  max_iter 반복(Actor 생성→Evaluator 채점) → `is_passed` 또는 `score>=pass_score` 면 통과 종료,
  아니면 비평 주입 후 재생성 → 미통과 시 **최고점본 폴백** + 경고 로그. 모듈: models(LoopSpec/
  Iteration/LoopResult)·prompts·clients(Completion/AnthropicCompletion/parse_evaluation)·
  orchestrator(run_loop)·config_loader(load_loop_spec). 예시 스펙 `config/loops/example.yaml`
  (비-PII 근거 문장 다듬기 — 거래처 실명·사업자번호 금지). 보안 제0원칙: 프레임워크는 받은
  문자열을 전달만 하고, input_data 의 비-PII 보장은 호출자 책임(루브릭에도 PII 금지 기준 포함).
  단위테스트 +12(총 146).

- 2026-06-22: [루프 백엔드: 로컬 CLI 우선] "로컬인데 왜 API를 쓰냐 — CLI 쓰면 되지" 지적 반영.
  **`CliCompletion`** 추가 — 설치된 Claude Code CLI(`claude -p ... --system-prompt
  --output-format json`)를 **구독 인증**으로 호출해 모델로 사용. 별도 API 키도, 추가 토큰
  과금도 없고 루프가 사람 개입 없이 스스로 돈다. `default_completion()` 이 claude CLI 우선 →
  없으면 API 키(`AnthropicCompletion`) 폴백 → 둘 다 없으면 에러. 실행기 `examples/run_loop_cli.py`
  추가. CLI 는 effort/스키마 강제가 없어 시스템 프롬프트에 JSON 스키마 지시를 덧붙이고
  parse_evaluation 으로 견고 파싱. 실측: rationale 루프를 CLI 로 자율 실행해 1회차 통과(95점)
  확인(JSONL 추적). 단위테스트 +6(총 152).

- 2026-06-22: [루프 스펙 2종 추가 + 3종 자율 실증] 로컬 CLI 로 세 제어흐름을 모두 실측.
  새 스펙 `config/loops/guide.yaml`(60대 비전문가용 단계별 안내 — 접근성 가치 직결),
  `config/loops/docfix.yaml`(초안 문단 명확·간결 편집 — 자율 개발 연계). 자율 실행 결과:
  ① example/rationale = **즉시 통과**(1회차 95점), ② docfix = **개선→통과**(80→85→90, 3회차),
  ③ guide = **미통과→최고점 폴백**(82→83→84→77, 최고점 84 채택 + 경고 로그). 셋 다 CLI(구독)로
  사람 개입 없이 생성·채점·재생성 — Evaluator 가 회차마다 구체적 비평 제시(특히 guide 는
  60대 접근성 기준이 엄격해 90점 미달, 폴백 정상 동작 확인). 모두 비-PII 합성 입력. 코드 변경 없음.

- 2026-06-22: [루프 수렴 개선 + 루브릭 정교화] (b/c) ① **퇴행 방지** — Actor 가 매 회차
  '처음부터 새로 쓰기'를 하던 것을, 직전 결과물을 함께 받아 '비평대로 고쳐쓰기'로 전환
  (prompts.render_actor_user 에 prev_output, orchestrator 가 직전 출력 주입). 잘된 부분을
  유지하고 지적분만 수정 → 회차 간 점수 퇴행 방지. ② **rationale 루브릭 실전화**
  (config/loops/example.yaml) — (제)/(판) 구분 적정성, 과세유형 정확성(카과=공제 가능/
  카면=면세·공제 불가, 공제 과장 금지), 불확실 시 검토/대안 표명, 담당자 30초 검증, PII 금지.
  ③ **guide 스펙 튜닝** — 단축키/용어 괄호 풀이, 폴더 열기 선행동작, 다건 반복, 안전한 담당자
  문의를 task 에 명시(max_iter 5). 실측(CLI 자율): guide 84(폴백)→**92 통과**(2회차, 직전 결과물
  고쳐쓰기로 수렴), rationale 면세(카면) 케이스 **92 통과**(면세 공제불가 정확 반영·(판) 구분).
  단위테스트 보강(prev_output 주입 검증). 모두 비-PII 합성 입력.

- 2026-06-22: [세무대리인 반복업무 자동화 발의 5건] `kafa/agent/` 신설 — 매달 반복·고소요
  업무의 데이터 불필요·PII 안전 코어를 구현. ① `prefile_check`(부가세 신고 전 자가검증
  체크리스트: 합계검산·공제정합·체크섬·부가율·중복), ② `bizno_batch`(사업자번호 일괄 검증·
  중복제거·홈택스 조회목록 — 실조회 보류), ③ `withholding`(원천징수 계산: 사업소득 3.3%/
  기타소득 8.8%, 율 config/agent.yaml 외부화, 원단위 절사·소액부징수 플래그), ④ `intake`
  (월별 자료 수취 체크리스트·누락 점검·요청 메시지 초안), ⑤ `recon`(전월 대비 거래처/계정
  변동 대사 — 거래처 해시 보존으로 PII 안전). 발의·As-Is/To-Be·보류는
  `docs/proposals/세무대리인_자동화_초안.md`. config_loader.load_agent 추가. 외부 노출은
  마스킹/해시/합계만. 보류(실데이터·서식·API 필요): 홈택스 상태조회·지급명세서 서식·매출
  교차검증·파일 자동인식. 단위테스트 +11(총 163).

- 2026-06-22: [CI 추가] `.github/workflows/ci.yml` — `pull_request`·main push 에서 pytest 실행
  (Python 3.11/3.12 매트릭스, `pip install -e ".[dev]"`). pull_request 이벤트는 PR 브랜치의
  워크플로 파일을 사용하므로 PR #19부터 즉시 검사 동작. 이전엔 PR 트리거 CI 가 없어
  체크런 0개였음 → 이제 진짜 CI 실패 감시 가능.

- 2026-06-23: [기능 검토 — 버그/엣지 수정] loop·agent 코드 리뷰(병렬 2건)로 발견한
  버그 3 + 엣지 4 수정. 버그: ① `run_loop` max_iter≤0 시 `max(빈 리스트)` 크래시 →
  빈 결과(stopped_reason="no_iterations") 안전 반환, ② `parse_evaluation` 가 문자열
  `"false"`를 `bool("false")==True`로 통과 처리 → `_coerce_bool`로 강제, ③ `withholding`
  음수 지급액 ValueError 가드(ROUND_DOWN 절사 오류·소액부징수 오플래그 방지). 엣지:
  ④ `parse_evaluation` 여러 JSON 객체 시 "Extra data" 크래시 → `_first_json_object`
  (raw_decode로 첫 유효 객체), ⑤ `CliCompletion` TimeoutExpired → RuntimeError 래핑,
  ⑥ `recon` 배치 내 계정변동 누락·미정(None) 거래처 매달 재신규 → live 기준선 비교 +
  '봤음' 센티넬(_SEEN_NO_CODE)·신규 dedupe, ⑦ `prefile_check` 합계검산에 tol 적용(엑셀
  float 오차 오탐 방지). 회귀 테스트 +9(총 172). 미수정(별도): agent/loop 제품 결선,
  prefile↔evidence/review 중복, VendorBaseline↔DupGuard 공용화.

- 2026-06-23: [제품 결선] standalone 이던 agent 5종을 실제 진입점에 연결. ① 행 기반 3종을
  변환 파이프라인 산출물로: `prefile_check`→`_prefile.txt`(항상), `bizno_batch`→`_bizno.txt`
  (행의 사업자번호를 로컬 처리·요약만 마스킹, 항상), `recon`→`_recon.txt`(기준선 저장소
  지정 시 opt-in; CLI `--recon-store`, MCP convert `recon_store`, run_batch 가 VendorBaseline
  누적·저장). `process_rows`/`_run_one`/`run_batch`/`convert`/`_masked_file` 에 결선(마스킹/집계만
  노출). ② 비-행 2종을 MCP 도구 + service 파사드로: `withholding`(원천징수 계산 — 금액·유형만,
  수령자 PII 없음), `intake`(자료 수취 체크리스트 — 고객명 대신 '{고객명}' 자리표시자, PII
  미입력). `service.withholding_calc`/`intake_checklist`. intake.build_intake_checklist 에
  mask 토글 추가. loop 는 의도적으로 미결선(개발용). 테스트 +5(총 177).

- 2026-06-23: [중복 정리] 검토에서 지적된 중복 로직 제거. ① `prefile_check` 가 불공제/검토/
  미등록(체크섬·미상)/부가율/중복을 자체 재계산하던 것을 **EvidenceReport 재사용**으로 변경,
  고유 검산(합계=공급가액+세액+비과세)만 직접 수행. `build_prefile_check(rows, evidence=...)`
  로 이미 만든 evid 주입 → `process_rows` 의 중복 스캔 제거(진실 소스 1개). ② 로컬 JSON 저장
  공용화 `kafa/jsonstore.py`(load_json/save_json) → `DupGuard`·`VendorBaseline` 가 공유(손상
  회복·부모 생성 일원화). 동작 동일(테스트 177 유지).

## 사용 형태 (사용자 관점)
- **Claude Desktop(MCP)**: `convert`/`preview` 도구. 변환은 결정론적 코드가 수행, 출력은
  고정 .xls 스키마, 결과는 마스킹 요약만 → "정해진 템플릿으로 항상 오차없이". 설정/사용 docs/usage.md.
- **CLI**: 동일 파이프라인. service.run_batch 공용.
- **보안**: raw PII 는 도구 결과(=LLM 컨텍스트)에 절대 미포함. .xls/CSV 는 로컬 디스크에만.
