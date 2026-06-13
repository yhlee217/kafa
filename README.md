# kafa
kafa는 세무회계사무소의 신용카드 매입 기장을 자동화하는 도구다. 위하고 T '신용카드' 화면에서 내려받은 엑셀(.xlsx)을 입력으로 받아, 위하고가 채우지 못한 '미추천' 행의 계정을 추천하고, 과세유형(카과 57 / 카면 58 / 일반 3)과 차·대변 계정을 결정한 뒤, 계정명을 코드로 변환해 업로드 양식(.xls)으로 출력한다. 규칙은 결정론적 룰 엔진으로 외부 LLM 호출 없이 동작하므로 **같은 입력이면 항상 같은 결과**가 나오고, 사업자번호·거래내역 등 PII는 로컬에서만 처리한다(AI에는 마스킹 요약만 전달).

## 사용법
- **Claude Desktop(MCP)**: `pip install -e ".[mcp]"` 후 `claude_desktop_config.json` 에
  `{"mcpServers":{"kafa":{"command":"kafa-mcp"}}}` 추가 → "이 파일 변환해줘"로 사용.
- **CLI**: `python -m kafa.cli <입력폴더|파일.xlsx> <출력폴더> [--client-type ...] [--truth ...]`

자세한 담당자용 설명은 [`docs/usage.md`](docs/usage.md), 설계·규칙은 [`docs/decisions.md`](docs/decisions.md).
