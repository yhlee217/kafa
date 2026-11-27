# 개발용 Windows PC 설치

아드님 개발용 Windows PC 에 kafa 를 설치해 코드를 고치고, 테스트를 돌리고, **가짜 자료**로 아버님 화면을
끝까지 돌려 보는 방법이다. 아버님 PC 설치는 `docs/install_windows.md`.
명령은 모두 PowerShell(Windows 터미널) 기준이다.

## 1. 도구 설치 (한 번)

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
setx PYTHONUTF8 1          # 한글 출력·파일 인코딩을 UTF-8로 (새 터미널부터 적용)
```

설치가 끝나면 **터미널을 닫고 새로 연다**. `py -0p` 에 3.12 가 보이고 `git --version` 이 나오면 된다.
Microsoft Store 판 python 은 쓰지 않는다 — `python` 을 쳤을 때 스토어가 열리면 설정 → 앱 → 고급 앱 설정 →
앱 실행 별칭에서 python 두 개를 끈다.

## 2. 코드 받기

```powershell
mkdir C:\dev -Force; cd C:\dev
git clone -c core.autocrlf=input https://github.com/yhlee217/kafa
cd kafa
git config user.name "이름"; git config user.email "GitHub 이메일"
```

- `core.autocrlf=input` — 줄바꿈을 맥과 같은 LF 로 유지한다(Windows 에서 커밋해도 파일 전체가 바뀐 것처럼
  보이지 않는다).
- 경로는 영문(`C:\dev\kafa`)을 권한다.

## 3. 가상환경과 설치

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
```

`kafa-desk`·`pytest` 를 그냥 치려면 가상환경을 켠다(처음 한 번 실행 정책을 푼다).

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned    # 처음 한 번
.venv\Scripts\Activate.ps1                              # 터미널을 열 때마다
```

## 4. 테스트

```powershell
.venv\Scripts\python -m pytest -q
```

전부 통과해야 한다. 테스트는 합성 데이터만 쓰고 개발자 PC 의 실제 운영 파일(`~/.kafa`, `~/kafa-out`)을 건드리지
않는다(`tests/conftest.py`). Windows 에서만 실패하는 게 있으면 실패한 테스트 이름과 오류 마지막 줄을 남긴다.

## 5. 가짜 자료로 답지 화면 써 보기

`tools/make_demo.py` 가 지어낸 수임처 2곳·거래처 8건짜리 받은자료를 만든다.

```powershell
cd C:\dev\kafa
.venv\Scripts\python tools\make_demo.py C:\dev\kafa-demo
.venv\Scripts\python -m kafa.desk_cli screen --base C:\dev\kafa-demo
```

브라우저가 열리면 **처리하기** → 확정 → **업로드본 만들기**까지 끝까지 해 봐도 된다(가짜 자료).
다시 하려면 `C:\dev\kafa-demo` 를 지우고 위 두 줄을 다시 실행한다.

아버님이 보실 .bat 까지 똑같이 보려면:

```powershell
.venv\Scripts\python -m kafa.desk_cli setup --base C:\dev\kafa-demo
```

그다음 `C:\dev\kafa-demo\답지_화면.bat` 을 더블클릭한다. `--base` 를 꼭 붙인다(빼면 `%USERPROFILE%\kafa` 에 만든다).

## 6. 개발할 때 지킬 것

- **실제 세무 자료는 이 PC 에 두지 않는다.** 원본은 아버님 PC(와 지금은 아드님 맥의 처리 이력)에만 있다.
  꼭 써야 하면 **저장소 폴더(`C:\dev\kafa`) 밖**에 둔다 — `.gitignore` 가 엑셀은 막지만 `.db` 는 막지 않는다.
- 작업은 브랜치에서 하고 PR 로 올린다. 첫 push 때 GitHub 로그인 창이 한 번 뜬다.
- 업데이트는 `git pull`. `pyproject.toml` 이 바뀌었으면 3번의 `pip install -e ".[dev]"` 도 다시.
- Claude Code 로 개발하려면 `C:\dev\kafa` 폴더를 연다 — `CLAUDE.md` 의 보안 원칙이 자동으로 적용된다.
