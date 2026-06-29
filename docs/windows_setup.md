# Windows 설치·자동 실행 가이드 (아버지 PC용)

목표: **inbox 폴더에 위하고 다운로드본을 넣기만 하면 자동 처리**되게 한다. 명령어 몰라도 됨.

## 1. 폴더 만들기 (예)
```
C:\kafa\inbox\      ← 위하고에서 받은 파일을 여기에(고객별 하위폴더 권장: C:\kafa\inbox\고객명\)
C:\kafa\out\        ← 결과·DB·아카이브가 자동 생성됨
```

## 2. 설치 (둘 중 하나)
**A) Python 사용**(개발자가 1회 셋업)
```
pip install -e .            # 또는 pip install kafa
pip install win10toast      # (선택) 완료 토스트 알림
```
**B) 단일 실행파일(.exe)** — 아버지 PC엔 Python 없이도 되게 (개발자가 빌드해 전달)
```
pip install pyinstaller
pyinstaller --onefile --name kafa-watch kafa/pipeline/watch_cli.py
#   → dist\kafa-watch.exe 생성 → 아버지 PC로 복사
```

## 3. 상시 자동 실행
### 방법 1 — 시작프로그램(가장 쉬움)
1. `Win + R` → `shell:startup` 입력 → 시작프로그램 폴더 열림
2. 아래 `kafa-watch.bat`(scripts/windows/kafa-watch.bat 참고)을 경로만 고쳐 넣기
3. 로그인하면 자동으로 감시 시작. 검은 창 하나가 떠 있으면 동작 중(끄지 말 것).

### 방법 2 — 작업 스케줄러(창 숨김)
- 작업 스케줄러 → 작업 만들기 → 트리거 "로그온할 때" → 동작 "프로그램 시작":
  `C:\kafa\kafa-watch.bat` → "숨김 실행" 옵션.

## 4. 사용 (아버지)
1. 위하고에서 신용카드 매입 자료 다운로드 → `C:\kafa\inbox\<고객명>\` 에 넣기
2. 잠시 후 **"kafa 처리 완료" 알림** → `C:\kafa\out\<고객명>\<기간>\` 에 업로드용 파일·점검표 생성
3. 그 `_upload.xls` 를 위하고에 업로드

## 5. 보안
- 전부 로컬에서 동작(인터넷으로 자료 안 보냄). DB·결과는 `C:\kafa\out` 안에만.
- 백업은 외장하드/NAS로(클라우드 X — 고객 개인정보).

## 6. 한 번만 처리하고 싶을 때
감시 없이 즉시 한 번:  `kafa-pipeline C:\kafa\inbox C:\kafa\out`
