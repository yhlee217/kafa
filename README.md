# kafa

위하고 T(WEHAGO T) **신용카드 매입** 전표를 자동 분류·생성하는 도구입니다.
세무회계사무소가 위하고 '신용카드' 화면에서 내려받은 엑셀(.xlsx)을 입력으로 받아,
위하고가 채우지 못한 **'미추천'(차변계정 미정) 행에 계정을 추천**하고, 과세유형·차/대변
계정을 결정한 뒤, **계정명을 코드로 변환해 '신용카드 매입 엑셀 업로드' 양식(.xls)**으로
출력합니다. 공개 API가 없어 **엑셀 라운드트립**으로 연동합니다(다운로드 → 변환 → 업로드).

```
위하고 '신용카드' 화면 ──다운로드──▶ download.xlsx
                                        │  kafa (이 도구)
                                        ▼
              upload.xls ──업로드──▶ 위하고 '신용카드 매입 엑셀 업로드'
```

---

## 핵심 가치

1. **미추천 해소** — `전표상태=미추천` 행에 차변계정을 추천하고 **그 근거를 한국어로** 남깁니다.
2. **일괄 코드 변환** — 계정명(한글)→계정코드, 유형 라벨→코드, 불공제 룰 적용, 중복전표 스킵.

위하고가 이미 채운 행은 **매핑만** 하고, 도구의 실제 일은 *미추천 계정 추천 + 일괄 변환*입니다.

---

## 두 가지 특징

### ① 항상 같은 결과(결정론) + 고정 양식
데이터 변환·코드화는 **결정론적 룰 엔진**이 수행합니다. 같은 입력이면 항상 같은 출력이 나오고,
출력은 고정 컬럼 스키마의 .xls로만 생성됩니다. AI가 추정한 미추천 계정도 **결정 캐시**로
같은 특징이면 같은 결과를 재사용합니다.

### ② 추가 비용 없는 AI 추정 (호스트 모델 사용)
미추천 행의 차변계정 추정은 **이미 켜져 있는 Claude Desktop/Code의 모델**이 수행합니다.
별도 API 키·추가 토큰 청구가 없습니다.

```
analyze(파일)  → 미추천 행의 '비-PII 특징'만 반환(업태/종목/품명/유형)
     ↓ 호스트 Claude 가 특징으로 차변계정 추정
convert(파일, 출력, recommendations=[{id, account_code, confidence, rationale}])
     → 허용 계정만 적용해 업로드용 .xls 생성
```
- 추천을 못 받은 건은 **자가 시딩**(같은 배치의 이미 분류된 행에서 사업자번호→거래처→유사도)
  으로 로컬 폴백 → 항상 결과가 나옵니다.
- 자동 배치에서 사람 개입 없이 AI 추정이 필요할 때만 별도 API 직접호출을 opt-in 할 수 있습니다
  (`config/rules.yaml`의 `recommend.llm.enabled: true` + `ANTHROPIC_API_KEY`, 이때만 별도 비용).

---

## 보안 제0원칙 (절대 위반 금지)

원천 세무데이터(**사업자번호·카드번호·거래처 실명 = PII**)는 **어떤 LLM/에이전트
컨텍스트에도 올리지 않습니다.** 로컬에 머무는 건 파이썬 코드 실행뿐입니다.

- raw 엑셀은 코드가 로컬에서 처리. 모델/리포트로는 **비-PII 특징·통계·마스킹 샘플**만.
- AI 추정에 넘기는 건 업태/종목/품명/유형뿐 — 거래처 실명·사업자번호는 전달하지 않습니다.
- 노출 시 `kafa/security.py`의 `mask_name`/`mask_bizno`/`hash_id`로 마스킹.

---

## 확정 도메인 규칙 (요약)

| 항목 | 규칙 |
|---|---|
| 과세유형 코드 | 3 일반 / 57 카과 / 58 카면 / 59 카영 / 63 화물. **불공제 전용 코드 없음** → 불공제는 유형 일반(3) + 국세청 컬럼으로 구분 |
| 대변(상대계정) | 법인 = **미지급비용(262)**, 거래처 = 카드사. 개인사업자는 보류 → 법인 폴백 + 검토 |
| 불공제 | 3단 분기: 기본=국세청 컬럼 / 자동 불공제 / **의심→검토**(비영업용 승용차 등, 자동 확정 금지) |
| 계정명→코드 | `(제)`/`(판)` 접두 정규화. 양식의 '계정과목(참고용)' 시트 파싱 자동생성 예정 |
| 중복전표 | `전표상태=중복전표` 스킵 + 리포트. 2차로 처리 키(해시) 로컬 보존 |
| 환불·취소(음수) | 원분류 유지, 차·대변 방향 반전 |
| 의제매입세액 | 카면(58)+고객사 음식점 → **플래그만**(율·한도 계산 안 함, 신고 단계로 이관) |

자세한 근거·규칙ID·결정 이력은 [`docs/decisions.md`](docs/decisions.md).

---

## 설치 · 실행

```bash
pip install -e .            # 기본(pandas/openpyxl/xlwt/xlrd/PyYAML)
pip install -e ".[mcp]"     # Claude Desktop/Code(MCP) 연동까지
python -m pytest -q         # 단위테스트(112케이스)
```

### A. Claude Desktop / Code 에서 (권장 — 가장 쉬움)
`claude_desktop_config.json`에 한 번만 등록한 뒤 자연어로 부탁합니다.
```json
{ "mcpServers": { "kafa": { "command": "kafa-mcp" } } }
```
> 대화창에 "바탕화면 `카드` 폴더의 `3월.xlsx`를 변환해서 `출력`에 넣어줘"라고 말하면,
> Claude가 분석→추정→생성까지 처리하고 한국어로 결과를 안내합니다.

MCP 도구 3종: `preview`(점검) · `analyze`(미추천 특징 반환) · `convert`(추정 적용 + .xls 생성).
모두 마스킹 요약/비-PII만 주고받습니다. 담당자용 자세한 설명: [`docs/usage.md`](docs/usage.md).

### B. 명령줄(CLI) — 자동화/일괄
```bash
python -m kafa.cli <입력폴더|파일.xlsx> <출력폴더> \
    [--client-type corporate|individual] [--dup-store dup.json] [--truth 정답.csv]
```
파일별 에러 격리(형식 오류는 건너뛰고 계속), 부분 실패 시 종료코드 1.

### 출력 파일
| 파일 | 용도 |
|---|---|
| `<파일>_upload.xls` | **위하고 업로드용** (2MB 초과 시 `_partNN.xls` 분할, CP949 안전화) |
| `<파일>_review.txt` | 담당자 검토 요약(미해소·검토·부가율 이상·미등록 의심) + 자동처리율 |
| `<파일>_review.csv` | 행별 분류 중간 산출물(추천 근거 포함, 수작업 대조용) |
| `<파일>_accuracy.txt` | `--truth` 지정 시 필드별 정확도·불일치 사유별 |
| `_manifest.json` | 배치 요약(파일별 건수·자동처리율·실패 사유) |

---

## 아키텍처

```
kafa/
  service.py          서비스 파사드: run_batch / analyze / convert / preview (마스킹·비-PII만)
  mcp_server.py       MCP 서버(Claude Desktop/Code) — preview/analyze/convert
  cli.py              입력폴더→출력폴더 파이프라인 / classify_rows
  config_loader.py    YAML 로더(규칙·계정코드 외부화)
  security.py         PII 마스킹/해시
  validate.py         사업자번호 체크섬 / 부가율 이상(순수 검증)
  eval.py             정확도 검증 하니스(수작업 정답 대조)
  dup_guard.py        중복 2차 안전장치(해시 키 로컬 보존)
  rules/              Phase 1 결정론적 룰 엔진(순수 함수 + 단위테스트)
    models.py vat_type.py deductibility.py accounts.py counterparty.py
    deemed_credit.py negative.py vendor_match.py engine.py
  recommend/          Phase 2 미추천 해소 (AI 대차변 추정 + 근거 생성)
    features.py       비-PII 특징 추출 + 서명(PII 차단)
    recommender.py    Recommender — PickRecommender(호스트 추정)/SeedRecommender(시드)/LLM(opt-in)
    seed.py           자가 시딩 인덱스(거래처·사업자번호별 최빈 계정)
    llm.py            (opt-in) Claude API 직접호출 추정
    explain.py        규칙ID + 맥락 → 한국어 근거 문장
  io_wehago/          엑셀 입출력(원천 데이터 로컬 전용)
    reader.py(.xlsx) writer.py(.xls/xlwt) account_sheet.py schema.py
  report/review.py    Phase 4 검토 리포트(담당자 전용) + 중간 산출물 CSV
config/
  rules.yaml          모든 규칙·코드·키워드 외부화
  account_codes.yaml  검증된 계정명→코드
tests/                표 기반 단위테스트(112케이스)
docs/
  decisions.md        Phase 0 매핑표·결정·규칙ID 색인·변경 이력
  usage.md            담당자(비전문가)용 사용 설명서
```

규칙·코드표는 코드에 하드코딩하지 않고 모두 `config/*.yaml`로 외부화합니다.

---

## 진행 상태 (spec v4)

- ✅ **Phase 0** 필드 매핑
- ✅ **Phase 1** 결정론적 룰 엔진 + 단위테스트
- ✅ **Phase 2** 미추천 해소 — 호스트 모델(Claude) 추정 + 근거 생성, 자가 시딩 폴백
- ✅ **Phase 3** 업로드 .xls 생성 — 2MB 자동 분할·CP949 안전화·필수값 검증
- ✅ **Phase 4** 검토 리포트 — 요약·체크포인트·부가율 이상·미등록 의심·중간 산출물 CSV
- ✅ 운영성 — 정확도 검증 하니스(수작업 대조), 자동처리율 KPI, 배치 매니페스트

### 보류 항목 (데이터 확보 시 확정)
1. 개인사업자 상대계정(인출금 등) — 현재 법인(262) 폴백 + 검토 플래그
2. 카면(58) 실제 처리·식당 의제 검증
3. 간이과세자 자동 불공제 식별자
4. 봉사료=비과세 동일성 / 거래구분 허용값 / .xlsx 업로드 허용 / 대변거래처 양식 위치
5. '계정과목(참고용)' 시트 파싱(매핑 자동생성)

---

## 자율 개발 (스스로 개발→검수→재개발)
프로젝트가 작은 개선을 반복적으로 쌓도록 **개발 → 자체 검수(/code-review) → 재개발** 루프를
운영한다. 무인 자동(GitHub Actions cron `.github/workflows/claude-autonomous.yml`)과 대화형
(`@claude`) 두 방식이 있으며, 둘 다 같은 플레이북·가드레일(PII 금지·PR-only·보류 추측 금지)을
따른다. 방법론·활성화 절차: [`docs/methodology.md`](docs/methodology.md),
플레이북: [`.github/AUTONOMOUS_DEV.md`](.github/AUTONOMOUS_DEV.md).

## 라이선스 / 용도
세무회계사무소 내부 신용카드 매입 기장 자동화 전용. 원천 세무데이터는 로컬에서만 처리합니다.
