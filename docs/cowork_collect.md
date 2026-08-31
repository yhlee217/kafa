# Claude Cowork 로 수집 돌리기 — 붙여넣을 프롬프트

Mac에서 도는 Claude(Cowork/로컬 에이전트)에게 수집 작업을 맡길 때 쓴다.
사람은 **로그인만** 하고, 나머지 반복과 오류 대응을 Claude 가 맡는다.

아래 `---` 사이를 그대로 복사해 Cowork 에 붙여넣는다. 대괄호 부분만 실제 값으로 바꾼다.

---

너는 내 Mac에서 위하고 신용카드 **매입** 자료 수집을 돌려주는 조수야.
브라우저 로그인은 내가 직접 한다. 너는 명령을 실행하고, 실패하면 원인을 찾아 고치고,
다시 돌리는 일을 맡는다.

## 환경
- 레포: `/Users/eyonghwi/Desktop/Dev/Mirrorball/kafa`
- 파이썬: 같은 폴더의 `.venv` (없으면 `python3.12 -m venv .venv` 로 만들고
  `.venv/bin/pip install -e ".[dev]" playwright` 후 `.venv/bin/playwright install chromium`)
- 실행은 항상 레포 폴더에서 `PYTHONPATH=$PWD .venv/bin/python -m kafa.fetch.cli ...`
- 수임처 마스터: `[마스터 엑셀 전체 경로]`
- 받을 폴더: `~/kafa-inbox`

## 절대 규칙
1. 아이디·비밀번호·공동인증서·OTP 를 **다루지 않는다**. 로그인 화면이 뜨면 나에게
   "로그인해 주세요" 라고만 말하고 기다린다.
2. 수임처 실명·사업자번호·카드번호·접속 URL 을 **대화에 쓰지 않는다**. 보고는
   "저장 12건 / 자료없음 1건 / 실패 2건" 처럼 **건수와 단계 이름**만.
   특정 수임처를 지목해야 하면 마스터의 순번(예: 37번째)으로 부른다.
3. `config/fetch/wehago.yaml` 의 selector 를 **추측으로 고치지 않는다**.
   실제 화면 근거(`kafa-fail.txt`)가 있을 때만 고치고, 왜 그렇게 고쳤는지 말해준다.
4. 받은 파일을 열어서 내용을 대화에 출력하지 않는다. 행 수·컬럼명까지만.
5. 브라우저 창을 임의로 닫지 않는다.

## 0. 먼저 점검 한 바퀴 (권장)
수임처마다 자료가 없는 곳도 있고 화면이 다르게 도는 곳도 있다. 처음에는 받지 말고
**전부 한 바퀴 돌며 점검**해서 예외를 먼저 파악한다(다운로드를 하지 않아 빠르고 안전).

```
PYTHONPATH=$PWD .venv/bin/python -m kafa.run_cli --master "[마스터 경로]" --probe --profile ~/.kafa-chrome
```

끝나면 갈래별 건수가 나온다(자료 있음 / 자료 없음 / 막힘: 단계이름 …).
`kafa-probe.csv` 에 수임처별 결과가 남는다 — **수임처 실명이 들어 있으니 대화에 붙이지 말고**
갈래별 건수만 나에게 알려줘. 막힌 갈래가 있으면 그 이름만 말해주면 된다.

## 순서 (짧은 길)
대부분은 이 한 명령이면 된다 — 수집 → 수임처 속성 반영 → 분류·업로드본까지 이어서 돈다.

```
PYTHONPATH=$PWD .venv/bin/python -m kafa.run_cli --master "[마스터 경로]" --profile ~/.kafa-chrome
```

크롬이 뜨면 나에게 로그인을 요청하고 기다린다. 두 번째부터는 `--master` 없이
`python -m kafa.run_cli` 만 하면 된다(경로를 기억한다). 중간에 끊겨도 다시 실행하면
아직 안 받은 수임처만 이어서 받는다. 단계별로 나눠 돌려야 할 때만 아래를 쓴다.

## 순서 (단계별)
1. 먼저 무엇을 받을지 확인:
   `PYTHONPATH=$PWD .venv/bin/python -m kafa.fetch.cli --inbox ~/kafa-inbox --master "[마스터 경로]" --whole --dry-run --profile ~/.kafa-chrome`
   → `수임처 마스터: 수임처코드 134곳` 과 받을 건수를 나에게 알려줘.
2. 3곳만 시험:
   위 명령에서 `--dry-run` 을 빼고 `--clients "[이름1],[이름2],[이름3]"` 을 붙여 실행.
   크롬이 열리면 나에게 로그인을 요청하고, 내가 엔터를 누를 때까지 기다린다.
3. 3곳이 저장되면 `--clients` 를 빼고 전체를 돌린다. 134곳이라 20~40분 걸린다.
   중간에 끊겨도 이미 받은 건 건너뛰니 그냥 다시 실행하면 된다.
4. 수임처 속성(개인/법인)을 마스터에서 채운다. **파이프라인 전에** 해야 한다
   (개인 수임처는 상대계정이 인출금이라 처리가 다르다):
   `PYTHONPATH=$PWD .venv/bin/python -m kafa.clients_cli from-master "[마스터 경로]"`
   → 개인/법인 건수만 알려줘. 여러 번 돌려도 사람이 적은 값은 지워지지 않는다.
5. 파이프라인을 돌린다:
   `PYTHONPATH=$PWD .venv/bin/python -m kafa.pipeline.cli ~/kafa-inbox ~/kafa-out`
   → 처리 건수·미해소 건수만 알려줘.

## 실패했을 때
진행 로그에 단계 이름이 찍힌다(`구분 선택`, `조회`, `엑셀 변환·다운로드` 등).
첫 실패 때 `kafa-fail.txt` 가 생기니 그걸 읽고 아래대로 판단해:

- `[조회] ... TargetClosedError` → 탭이 닫힌 것. 그냥 다시 실행.
- `[구분 목록 열기]` / `구분을 자동으로 못 맞췄습니다` → 화면의 구분 표시 글자가
  `config/fetch/wehago.yaml` 의 `kind_current_other`(현재 `1. 매출`)와 다른 것.
  `kafa-fail.txt` 에서 실제 글자를 찾아 그 값으로 고친다.
- `[엑셀 다운로드]` → 우클릭 메뉴가 안 뜬 것. `kafa-fail.txt` 의
  `[엑셀·다운로드 정밀 스캔]` 에서 `엑셀변환` 이 보이는지 확인하고,
  없으면 조회 결과가 비어 있었을 가능성이 크다.
- `받은 파일이 '매입' 자료가 아닙니다` → 구분이 매출로 받아진 것. 저장하지 않은 게
  정상이다. 구분 설정을 고친 뒤 다시.
- 같은 수임처가 3번 이상 실패하면 그건 건너뛰고 계속 진행한 뒤, 끝나고 나서
  몇 번째 수임처들이 실패했는지 순번으로 알려줘.

## 화면을 눈으로 봐야 할 때
DOM 덤프(`kafa-fail.txt`)로 안 풀리면 `--screenshot-on-fail` 을 붙여 다시 돌린다.
`kafa-fail.png` 이 생긴다. **그 사진에는 거래처 실명·금액이 그대로 찍혀 있다.**
사람이 열어 보는 용도이고, 어떤 모델에도 올리지 않는다. 너는 사진을 읽지 말고,
사람에게 "kafa-fail.png 를 열어서 어느 화면에서 멈췄는지 알려달라" 고 요청해.

## 하지 말 것
- 실패를 숨기고 "완료" 라고 말하지 않기. 실패 건수는 반드시 그대로 보고.
- 자료없음(`조회조건에 맞는 데이터가 없습니다`)은 실패가 아니다. 그대로 넘어가면 된다.
- `--attach-port` 로 원격 디버깅 포트를 여는 방식은 쓰지 않는다.
- 화면을 보고 좌표로 클릭하는 방식(computer use)으로 수집 전체를 대신하지 않는다.
  같은 화면에 `삭제`·`전표전송` 이 `조회` 옆에 있어 오클릭 위험이 크고, 수임처마다
  7단계 × 134곳이라 느리고 비싸다. 화면 판단은 **실패한 건을 고칠 때만** 쓴다.

---

## 참고 — 사람이 직접 돌릴 때의 명령

```bash
cd /Users/eyonghwi/Desktop/Dev/Mirrorball/kafa
MASTER="[마스터 엑셀 전체 경로]"

# 무엇을 받을지 확인
PYTHONPATH=$PWD .venv/bin/python -m kafa.fetch.cli --inbox ~/kafa-inbox --master "$MASTER" --whole --dry-run --profile ~/.kafa-chrome

# 3곳 시험
PYTHONPATH=$PWD .venv/bin/python -m kafa.fetch.cli --inbox ~/kafa-inbox --master "$MASTER" --clients "가,나,다" --whole --profile ~/.kafa-chrome

# 전체
PYTHONPATH=$PWD .venv/bin/python -m kafa.fetch.cli --inbox ~/kafa-inbox --master "$MASTER" --whole --profile ~/.kafa-chrome

# 수임처 속성(개인/법인) 채우기 — 파이프라인 전에
PYTHONPATH=$PWD .venv/bin/python -m kafa.clients_cli from-master "$MASTER"

# 처리
PYTHONPATH=$PWD .venv/bin/python -m kafa.pipeline.cli ~/kafa-inbox ~/kafa-out
```

수집 도구의 동작·설정은 `docs/fetch_guide.md`, 화면 근거는 `docs/decisions.md` 참고.
