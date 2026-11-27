# 아버님 PC(Windows) 설치·업데이트 — Claude Code 에 붙여넣는 프롬프트

아버님 PC 에 kafa 를 설치할 때 **Claude Code(데스크톱 앱 Code 탭 또는 터미널)** 에 그대로 붙여넣는 프롬프트다.
쓰는 법(매달 할 일)은 `docs/desk.md`, 결정 배경은 `docs/decisions.md` 2026-11-27.

## 무엇을 어떻게 옮기나 — USB 없이

| 옮길 것 | 실명 | 방법 |
|---|---|---|
| 프로그램 코드 | 없음 | GitHub 에서 받는다. 공개 저장소라 로그인이 필요 없다(`git clone`). 업데이트는 `git pull` |
| 수임처 속성(`clients.yaml`) | 있음 | 아버님이 채워 보내신 **조사표 엑셀**로 아버님 PC 에서 다시 만든다(`kafa-clients import`) |
| 처리 이력(`kafa.db`) | 있음 | **옮기지 않는다.** 첫 달에는 미추천 행의 '지난달 같은 가맹점' 추천이 덜 붙고, 그 몫은 클로드 추천과 아버님 답지가 채운다. 이력은 대부분 위하고 자동 분류였다(domain_notes 2026-11-26) |

실명이 든 파일은 GitHub(공개)·메일·메신저로 보내지 않는다. 첫 달부터 이력 추천을 쓰고 싶으면 `kafa.db` 하나만
USB 나 같은 사무실 공유 폴더로 옮기고, 아래 5번에서 `setup --import-db <옮긴 kafa.db>` 로 넣는다.

## 붙여넣기 전에

- 아버님 PC 에서 **채워 보내신 조사표 엑셀의 위치**를 확인한다(예: `C:\Users\…\Downloads\수임처_조사표.xls`).
  아래 프롬프트의 `<조사표 경로>` 를 그 경로로 바꾼다.

## 설치 프롬프트

```text
이 Windows PC에 세무 보조 프로그램 kafa를 설치하고 동작을 확인해 줘. PowerShell로 진행해.

## 지켜야 할 것 (가장 중요)
- 이 PC의 세무 자료에는 수임처·거래처 실명과 사업자번호가 들어 있다. 다음 파일은 절대 열거나 읽거나
  내용을 출력하지 마: *.db, clients.yaml, *.xlsx, *.xls, %USERPROFILE%\kafa\ 와 %USERPROFILE%\.kafa\ 아래 파일 내용 전부.
  경로를 명령에 넘기고 실행만 하고, 결과는 명령이 출력한 건수만 보고해.
- 명령 출력에 수임처 이름이 보이면 그 줄은 나에게 옮기지 말고 "이름이 포함된 줄 N개"라고만 말해.
- 위하고 사이트에 접속하거나 로그인하지 마. 자동 수집 기능(kafa-fetch)은 쓰지 않는다.
- 무엇이든 새로 설치하기 전에 나에게 먼저 물어봐. Windows 설정은 바꾸지 마.
- 프로그램 코드는 고치지 마. 테스트가 실패하면 실패한 테스트 이름과 오류 마지막 줄만 보고하고 멈춰.

## 순서
1. 도구 확인
   - 파이썬: `py -0p` 로 3.11 이상이 있는지 본다. 없으면 나에게 묻고
     `winget install -e --id Python.Python.3.12` 로 설치한다(Microsoft Store 판 python 은 쓰지 않는다).
   - git: `git --version`. 없으면 나에게 묻고 `winget install -e --id Git.Git` 로 설치한다.
     설치 직후 이 창에서 git 이 안 잡히면 "C:\Program Files\Git\cmd\git.exe" 를 직접 쓴다.
2. 코드 받기(공개 저장소라 로그인이 필요 없다):
   git clone https://github.com/yhlee217/kafa C:\kafa-app
   이미 C:\kafa-app 이 있으면 지우지 말고 나에게 물어봐.
3. 가상환경과 설치:
   py -3.12 -m venv C:\kafa-app\.venv   (3.12가 없으면 설치된 3.11 이상 버전으로)
   C:\kafa-app\.venv\Scripts\python -m pip install --upgrade pip
   C:\kafa-app\.venv\Scripts\python -m pip install -e "C:\kafa-app[dev]"
4. 테스트(합성 데이터만 쓴다): C:\kafa-app 에서
   $env:PYTHONUTF8=1; C:\kafa-app\.venv\Scripts\python -m pytest -q
   마지막 줄(몇 개 통과)만 보고한다.
5. 아버님 폴더와 실행 아이콘:
   $env:PYTHONUTF8=1
   C:\kafa-app\.venv\Scripts\python -m kafa.desk_cli setup
   %USERPROFILE%\kafa 에 1_받은자료·2_클로드·3_답지·4_업로드본·_내부 폴더와
   답지_화면.bat · 처리하기.bat · 업로드본_만들기.bat 이 생겼는지 이름만 확인한다.
6. 수임처 속성 — 아버님이 채운 조사표 엑셀에서 만든다(파일은 열지 말고 경로만 넘긴다):
   C:\kafa-app\.venv\Scripts\python -m kafa.clients_cli import "<조사표 경로>"
   출력의 건수 줄(수임처 N곳, 개인/법인, 직원 있음/없음, 메모 N곳)만 보고한다.
   이어서 조사표 메모로 식대 계정을 지정한다(이름을 치지 않는다):
   C:\kafa-app\.venv\Scripts\python -m kafa.clients_cli meal 접대비 --where-note "식대-접대비"
   "[중단] 맞는 수임처가 N곳" 이 나오면 그대로 보고만 한다.
7. 수임처별 받은자료 폴더 만들기(조사표의 수임처 이름 그대로 — 처리할 때 이 이름으로 수임처를 찾는다).
   이름은 출력하지 말고 개수만 출력하게 해서 실행한다:
   C:\kafa-app\.venv\Scripts\python -c "from pathlib import Path; from kafa.config_loader import load_clients; b=Path.home()/'kafa'/'1_받은자료'; cs=list(load_clients()['clients']); [(b/c).mkdir(parents=True, exist_ok=True) for c in cs]; print(len(cs))"
8. 바탕화면 바로가기: [Environment]::GetFolderPath('Desktop') 에 답지_화면 · 처리하기 · 업로드본_만들기
   세 개의 바로가기(.lnk)를 만든다. 대상은 %USERPROFILE%\kafa\<이름>.bat, 시작 위치는 %USERPROFILE%\kafa.
9. 화면 동작 확인(아직 받은 자료가 없어서 실명이 나오지 않는다):
   C:\kafa-app\.venv\Scripts\python -m kafa.desk_cli screen --no-browser --port 8765 를 백그라운드로 띄우고
   Invoke-RestMethod http://127.0.0.1:8765/api/state 의 응답에서 sheet 값이 비어 있는지(null)만 확인한 뒤
   그 프로세스를 끈다.

## 마지막 보고 (이 형식으로, 실명 없이)
- 파이썬 버전 / git 버전 / 설치 위치
- 테스트: N개 통과
- 조사표: 수임처 N곳 (개인 a / 법인 b, 직원 있음 c / 없음 d), 식대 계정 지정 결과
- 받은자료 폴더 N개, 실행 아이콘 3개, 바탕화면 바로가기 3개
- 화면 확인: 정상 / 문제(오류 마지막 줄)
```

## 업데이트 프롬프트

```text
C:\kafa-app 의 kafa 프로그램을 최신으로 올려 줘. 세무 자료 파일(*.db, *.yaml, *.xlsx, *.xls, %USERPROFILE%\kafa\ 아래)은 열지 마.
1. git -C C:\kafa-app pull
2. C:\kafa-app\.venv\Scripts\python -m pip install -e "C:\kafa-app[dev]"
3. $env:PYTHONUTF8=1; C:\kafa-app 에서 C:\kafa-app\.venv\Scripts\python -m pytest -q — 마지막 줄만 보고
4. C:\kafa-app\.venv\Scripts\python -m kafa.desk_cli setup — 실행 아이콘을 새로 쓴다(아버님 폴더와 처리 이력은 그대로)
실패하면 오류 마지막 줄만 보고하고 멈춰.
```

## 설치 뒤 아버님과 같이

- **수임처 폴더**: 설치 7번에서 조사표 이름으로 만들어 둔다. 위하고 파일은 그 폴더에 넣는다. 새 수임처는
  조사표 이름과 똑같이 폴더를 만든다(이름이 다르면 개인/법인·직원 유무를 찾지 못하고 이름 규칙·기본값을 쓴다).
- **식당 식대**: 통화에서 들은 '식대를 접대비로 넣는 식당'은 아버님 PC 에서 한 곳씩 지정한다(아드님).
  `C:\kafa-app\.venv\Scripts\python -m kafa.clients_cli meal 접대비 --client "이름"`
  화물차·음식점 표시도 같은 방식: `… kafa.clients_cli flag 화물차 --client "이름"`
- **첫 시험**: 한 수임처 파일로 답지_화면 → 처리하기 → 확정 몇 건까지만. **업로드본 만들기**는 실제 운영 때
  누른다(누르면 받은자료 원본이 보관함으로 옮겨지고 답이 처리 이력에 쌓인다).
- 설치한 뒤로는 **아버님 PC 가 원본**이다. 아드님 Mac 에서 같은 달을 따로 처리하지 않는다(이력이 갈라진다).
