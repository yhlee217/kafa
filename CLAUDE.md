# CLAUDE.md — kafa 프로젝트 가이드

위하고 T 신용카드 **매입** 전표 분류·생성 도구. 엑셀 라운드트립.
입력 = 위하고 '신용카드' 다운로드본(.xlsx, 위하고 1차 분류 결과),
출력 = '신용카드 매입 엑셀 업로드' 양식(.xls).

## 핵심 가치
1. **미추천 해소** — `전표상태=미추천`(차변계정 미정) 행에 계정 추천.
2. **일괄 코드 변환** — 계정명(한글)→코드, 유형 라벨→코드.
위하고가 이미 채운 행은 **매핑만** 한다.

## 보안 제0원칙 (절대 위반 금지)
원천 세무데이터(사업자번호·카드번호·거래처 실명 = PII)는 **어떤 LLM/서브에이전트
컨텍스트에도 올리지 않는다.** 로컬에 머무는 건 파이썬 코드 실행뿐.
- raw 엑셀은 코드가 로컬 처리. 에이전트로는 컬럼명·dtype·통계·**마스킹 샘플**만.
- 노출 시 `kafa/security.py`의 `mask_name`/`mask_bizno`/`hash_id` 사용.

## 아키텍처
```
kafa/
  service.py          서비스 파사드: run_batch / analyze(미추천 비-PII 특징) / convert(picks 적용) / preview
  mcp_server.py       MCP 서버(Claude Desktop/Code) — preview/analyze/convert, 마스킹·비-PII만
  cli.py              입력폴더→출력폴더 파이프라인(service.run_batch 위의 출력기)
  config_loader.py    YAML 로더(규칙/계정코드 외부화)
  security.py         PII 마스킹/해시
  validate.py         사업자번호 체크섬 / 부가율 이상 (순수 검증, Phase 4용)
  eval.py             정확도 검증 하니스(수작업 정답 대조, 자동처리율·불일치 사유별)
  dup_guard.py        1.8 중복 2차 안전장치(해시 키 로컬 보존)
  rules/              Phase 1 결정론적 룰 엔진(순수 함수 + 단위테스트)
    models.py         InputRow / ClassifiedRow / Verdict / Deduct
    vat_type.py       1.1 유형 라벨↔코드, 도출
    deductibility.py  1.3 불공제 3단(국세청/자동/검토)
    accounts.py       1.7 계정명→코드 ((제)/(판) 접두 정규화)
    counterparty.py   1.2 대변(법인 262 / 개인 TODO)
    deemed_credit.py  1.6 의제 플래그만(율/한도 계산 안 함)
    negative.py       1.9 환불/취소 방향 반전
    vendor_match.py   거래처 정확→후보→미매칭
    engine.py         오케스트레이션 classify_row
  io_wehago/          엑셀 입출력(원천 데이터 로컬 전용)
    reader.py         .xlsx 읽기(pandas), 요약행 제외
    writer.py         .xls 쓰기(xlwt, CP949) — Phase 3 골격
    account_sheet.py  '계정과목(참고용)' 시트 파서 — 구조(헤더) 기반 자동 매핑
    schema.py         입출력 컬럼 상수
  recommend/          Phase 2 미추천 해소 (핵심: AI 대차변 추정 + 근거 생성)
    features.py       비-PII 특징(업태/종목/품명/유형) 추출 + 서명(PII 차단)
    llm.py            Claude API 차변계정 추정(구조화 출력·계정명 enum·결정 캐시)
    recommender.py    Recommender(LLM 우선/시드 폴백), build_recommender, 자가 시딩
    explain.py        규칙ID+맥락 → 한국어 근거 문장
  report/             Phase 4 검토 리포트(담당자 전용) + 세무대리인 서비스 확장
    review.py         요약·부가율 이상·미등록 의심·추천내역 + 중간산출물 CSV(근거 포함)
    vat_summary.py    부가세 신고 보조 집계(과세공제/불공제/면세/의제/검토 합산)
  agent/              세무대리인 보조(신고전점검·사업자번호검증·원천징수·자료수취·대사)
  learn/              처리 이력 → 보류 규칙 추정(infer, 자동적용 금지·근거 제시) + CLI(kafa-learn)
  store/              베이스 데이터 SQLite 누적(VoucherStore, 멱등 upsert) — 로컬 단일 원본
  pipeline/           inbox 일괄 처리(runner)·폴더 감시(watch)·알림(notify)·진행 보드(summary) + CLI(kafa-pipeline/kafa-watch/kafa-board)
  loop/               루프 엔지니어링(Actor↔Evaluator 생성→평가→재생성)
    models.py         LoopSpec(스펙)/Iteration/LoopResult
    prompts.py        Actor/Evaluator 표준 프롬프트(스펙으로 렌더링)
    clients.py        주입형 Completion(공급자 비종속)·AnthropicCompletion·parse_evaluation
    orchestrator.py   run_loop — 통과/최고점 폴백 + JSONL 추적성
    config_loader.py  config/loops/*.yaml → LoopSpec 로더
config/
  rules.yaml          모든 규칙·코드·키워드 외부화
  account_codes.yaml  검증된 계정명→코드(시트 파싱분과 머지 예정)
  loops/example.yaml  루프 스펙 예시(비-PII 근거 문장 다듬기)
tests/                Phase 1 표 기반 단위테스트
```

## 규칙/코드표는 코드에 하드코딩하지 않는다
모두 `config/*.yaml` 로 외부화. 변경은 YAML에서. 결정 이력은 `docs/decisions.md`.

## 자율 개발 (개발→검수→재개발 루프)
무인 자동(`.github/workflows/claude-autonomous.yml`, cron)·대화형(`@claude`, `claude.yml`)으로
작은 개선을 반복. 플레이북 `.github/AUTONOMOUS_DEV.md`, 방법론 `docs/methodology.md`.
가드레일: PII 금지·합성데이터만·보류 추측 금지·PR-only·근거는 decisions.md.

## 실행
```bash
pip install -e .            # 또는 pip install pandas openpyxl xlwt xlrd PyYAML pytest
pip install -e ".[mcp]"     # Claude Desktop(MCP) 연동까지
python -m pytest -q         # 단위테스트
python -m kafa.cli <입력폴더|파일.xlsx> <출력폴더> \
    [--client-type corporate|individual] [--dup-store dup.json] [--truth 정답.csv]
python -m kafa.mcp_server   # MCP 서버(콘솔 스크립트 kafa-mcp). 담당자 사용법: docs/usage.md
```
Claude Desktop: `claude_desktop_config.json` 에 `{"mcpServers":{"kafa":{"command":"kafa-mcp"}}}`.
도구 `convert`/`preview` 는 raw PII 없이 **마스킹 요약만** 반환(데이터 변환은 결정론적 코드).
서비스 운영(배치): 파일별 에러 격리(형식 오류는 건너뛰고 계속), 출력 폴더에
`<파일>_upload.xls`(2MB 초과 시 `_partNN`), `_review.txt`/`_review.csv`(담당자),
`--truth` 지정 시 `_accuracy.txt`(자동처리율·필드별 정확도·불일치 사유별), `_manifest.json`(배치 요약).
부분 실패 시 종료코드 1.

## 진행 상태 (spec v4)
- ✅ Phase 0 (필드 매핑) — `docs/decisions.md`
- ✅ Phase 1 (결정론적 룰 엔진 + 단위테스트) — 완료
- ✅ Phase 2 (미추천 해소) — 동작. **AI(Claude) 차변계정 추정 + 근거 생성**이 1차.
  기본 방식 = **호스트 모델(Claude Desktop/Code)**이 추정(별도 API 키·추가 토큰 청구 없음):
  MCP `analyze`가 미추천 행의 비-PII 특징(업태/종목/품명/유형)만 모델에 주고, 모델이 정한
  계정을 `convert(recommendations=...)`로 받아 `PickRecommender`가 적용(허용 계정만 채택).
  거래처/사업자번호는 모델에 미전달. 추천이 없으면 **자가 시딩**(사업자번호→거래처→유사도)
  로컬 폴백. 별도 API 직접호출은 `recommend.llm.enabled`로 opt-in(기본 off, 추가 비용 시에만).
- ✅ Phase 3 (업로드 .xls 생성) — 동작. 2MB 자동 분할·CP949 안전화·필수값 검증.
  거래구분 허용값만 [보류](config 기본 공란)
- ✅ Phase 4 (검토 리포트) — 동작. 한 화면 요약 + 체크포인트, 미해소/검토,
  추천내역(근거·신뢰도), 부가율 이상, 홈택스 미등록 의심(사업자번호 체크섬),
  중간 산출물 CSV(담당자 전용, 수작업 대조용). 외부 노출 요약은 마스킹

## 보류 항목 (데이터 확보 시 확정)
1. ✅ 개인사업자 상대계정 — 담당자 확인(2026-08): 인출금/미지급금. 인출금 코드 미확보로
   미지급금(253) 적용 + 확인 플래그. 코드 확보 시 config 교체.
2. ✅ 봉사료 — 담당자 확인: 접대성 경비 → 불공제(DED-005). 단 '비과세=봉사료' 동일성은
   미확인이라 검토 플래그 유지(`service_charge.confirmed_identity`).
3. ⏸️ 간이과세자 자동 불공제 식별자 — 실샘플 확인 결과 '구분'은 법인/일반뿐이라 **식별 불가**
4. ⏸️ 카면(58) 실제 처리·식당 의제 검증 — 실샘플에 면세 행 없음. 의제 율은 매출 규모별이라
   신고 단계 이관(설계 확인됨)
5. ✅ 계정과목 코드표 — 양식의 '계정과목(참고용)' 시트에서 **332종 확보**(모호한 19종 제외)
6. ✅ 대변거래처 양식 위치 — 양식은 13열이며 **카드사 칸 없음**(내부 산출로만 보관)
7. ⏸️ 거래구분 허용값 / .xlsx 업로드 허용 여부
자세한 내용·근거는 `docs/decisions.md`.
