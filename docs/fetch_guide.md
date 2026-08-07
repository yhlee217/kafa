# 감독형 수집(kafa-fetch) 사용법

여러 거래처·여러 달의 다운로드본을 매번 손으로 받는 반복을 줄이는 도구다.
**로그인은 사람이 하고, 스크립트는 반복 클릭만 대신한다.**

## 먼저 확인할 것 (사용자 책임)
서비스 **이용약관이 자동화(스크립트) 접근을 금지하는지** 확인한다. 금지라면 쓰지 않는다.
이 도구는 계정 정지 위험을 대신 져 주지 않는다.

## 안전선 (설계상 고정)
- 아이디·비밀번호·공동인증서·OTP 를 **코드가 다루지 않는다**. 사람이 직접 로그인한다.
- 비공개 API 역공학 없음 — 사람이 화면에서 하는 동작만 한다.
- 요청 사이에 대기(`delay_seconds`)를 둬 서버 부담을 줄인다.
- 무인 상시 실행이 아니라, 필요할 때 사람이 켜고 지켜보는 도구다.

## 설치
```bash
pip install -e ".[fetch]"
playwright install chromium      # 최초 1회
```

## 1) 화면 보정 (최초 1회, 필수)
selector 를 추측해 넣으면 엉뚱한 곳을 클릭하므로, 실제 화면을 보고 채운다.
```bash
kafa-fetch --inspect
```
브라우저가 열리면 **직접 로그인**하고 신용카드 매입 화면까지 이동한 뒤 엔터.
화면의 후보 요소가 출력되고 **`kafa-inspect.txt`** 로도 저장된다(화면 구조만 — 입력값·
거래처명은 담기지 않는다). 그 파일을 보고 `config/fetch/wehago.yaml` 의 `selectors` 를 채운다.

```yaml
selectors:
  client_search_input: "#custSearch"          # 예시 — 실제 값으로
  client_result_item: "text={client}"         # {client} 는 거래처명으로 치환됨
  period_from_input: "#dateFrom"
  period_to_input: "#dateTo"
  search_button: "button:has-text('조회')"
  excel_download_button: "button:has-text('엑셀')"
```

## 2) 받을 목록 확인 (브라우저 없이)
```bash
kafa-fetch --inbox C:\kafa\inbox --clients 고객목록.txt --months 6 --dry-run
```
`고객목록.txt` 는 한 줄에 거래처 하나. 이미 받은 파일·처리 완료(`--archive`)는 자동 제외된다.

## 3) 수집
```bash
kafa-fetch --inbox C:\kafa\inbox --clients 고객목록.txt --months 6 \
           --archive C:\kafa\out\_archive
```
로그인 후 엔터 → 거래처×기간을 순회하며 `inbox\<거래처>\<기간>.xlsx` 로 저장한다.
한 건 실패해도 다음으로 계속 진행하고, 끝에 실패 목록을 보여준다. 중단해도 다시 실행하면
받은 것은 건너뛴다.

## 4) 처리
```bash
kafa-pipeline C:\kafa\inbox C:\kafa\out
```

## 이미 띄운 크롬에 붙이기 (선택)
```bash
chrome.exe --remote-debugging-port=9222
kafa-fetch --attach-port 9222 ...
```

## 왜 많이 모아야 하나
이력이 있으면 미추천 추정 정확도가 높다(실측: 정답 있는 72건 홀드아웃에서 **96%**).
미해소가 남는 주된 이유는 "그 가맹점을 처음 봐서"이므로, 달이 쌓일수록 자동처리율이 오른다.
