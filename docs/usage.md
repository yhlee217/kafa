# kafa 사용법 — 담당자용

위하고 '신용카드' 화면에서 내려받은 엑셀(.xlsx)을 **업로드용 .xls** 로 바꿔 줍니다.
데이터 변환은 100% 정해진 규칙(코드)이 하므로 **같은 입력이면 항상 같은 결과**가 나오고,
사업자번호·거래처 실명 같은 민감정보는 **AI에게 전달되지 않습니다(로컬에서만 처리)**.

쓰는 방법은 두 가지입니다. 편한 쪽을 고르세요.

---

## A. Claude Desktop 에서 쓰기 (추천 — 가장 쉬움)

자연어로 "이 파일 변환해줘" 하면 됩니다. 한 번만 설정하면 계속 그대로 동작합니다.

### 1) 설치 (한 번만)
```bash
pip install -e ".[mcp]"      # kafa 폴더에서. mcp SDK 포함 설치
```

### 2) Claude Desktop 설정 (한 번만)
설정 파일 `claude_desktop_config.json` 에 아래를 추가하고 Claude Desktop 을 재시작합니다.
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kafa": {
      "command": "kafa-mcp"
    }
  }
}
```
`kafa-mcp` 가 PATH 에 없으면 파이썬으로 직접 실행하도록 적습니다(가상환경 파이썬 경로 사용):
```json
{
  "mcpServers": {
    "kafa": {
      "command": "/path/to/python",
      "args": ["-m", "kafa.mcp_server"]
    }
  }
}
```

### 3) 사용
Claude Desktop 대화창에서 이렇게 말하면 됩니다(파일 경로만 알려주세요):
- "`/Users/me/카드/3월.xlsx` 변환해서 `/Users/me/카드/출력` 에 넣어줘"
- "그 파일 먼저 점검(preview)해줘" — 행수, 미추천/중복 건수, (마스킹된) 거래처 샘플을 보여줍니다.

AI 는 결과로 **마스킹된 요약만** 받습니다(예: "작성 120건, 자동처리율 96%, 담당자 확인 5건").
실제 업로드용 `*_upload.xls` 와 검토 파일은 지정한 출력 폴더에 저장됩니다.

도구 2개:
- `convert(input_path, output_dir, client_type="corporate", truth=None)` — 변환 실행
- `preview(input_path)` — 변환 전 점검

---

## B. 명령줄(CLI) 에서 쓰기

```bash
pip install -e .             # 한 번만
python -m kafa.cli <입력폴더|파일.xlsx> <출력폴더> [옵션]
```
옵션:
- `--client-type corporate|individual` 기장 클라이언트 유형(기본 법인)
- `--dup-store dup.json` 재업로드 사고 방지(처리한 전표 키를 로컬 보존)
- `--truth 정답.csv` 담당자 수작업 정답과 대조해 정확도 리포트 생성

예:
```bash
python -m kafa.cli ~/카드/3월.xlsx ~/카드/출력 --client-type corporate
```

---

## 출력 파일 (출력 폴더에 생성)
| 파일 | 용도 |
|---|---|
| `<파일>_upload.xls` | **위하고에 업로드**할 파일 (2MB 초과 시 `_part01.xls`…로 분할) |
| `<파일>_review.txt` | 담당자 검토 요약(미해소·검토·부가율 이상·미등록 의심) |
| `<파일>_review.csv` | 행별 분류 중간 산출물(수작업 대조용, 담당자 전용) |
| `<파일>_accuracy.txt` | `--truth`/`truth` 지정 시 정확도 리포트 |
| `_manifest.json` | 배치 요약(파일별 건수·자동처리율·실패 사유) |

## 업로드 순서
1. (선택) preview 로 점검 → 2. convert/CLI 로 변환 → 3. `_review.txt` 의 **담당자 확인 건** 처리
→ 4. `<파일>_upload.xls` 를 위하고 '신용카드 매입 엑셀 업로드' 에 올리기.

## 보장
- **오차 없음/고정 템플릿**: 변환은 결정론적 룰 엔진이 수행, 출력은 고정 컬럼 스키마(.xls)로만.
- **보안**: 원천 데이터는 로컬 코드만 처리. AI(Claude) 에게는 마스킹 요약만 전달.
