# 자율 개발 방법론 — 개발→검수→재개발 반복 루프

이 문서는 kafa가 **스스로 점진 개발하고, 자체 검수하고, 다시 개발에 들어가는** 구조의
방법론을 설명한다. 한 번 셋업하면 사람이 매번 지시하지 않아도 프로젝트가 작은 개선을
반복적으로 쌓는다. (다른 프로젝트에도 그대로 옮겨 쓸 수 있게 일반화해 적었다.)

---

## 1. 핵심 사이클

```
        ┌─────────────────────────────────────────────────────────┐
        ▼                                                         │
  ① 작업 선택        ② 구현            ③ 검증         ④ 자체 검수   │
  (플레이북 우선순위) (작은 단위 하나)  (pytest 통과)  (/code-review)│
        │                                                │         │
        │                                                ▼         │
        │                                       ⑤ 발견 문제 수정    │
        │                                          → 재검증         │
        │                                                │         │
        └──────────── ⑦ 다음 사이클 ◀── ⑥ 통합(기록·커밋·PR) ◀──────┘
```

- **① 작업 선택** — `.github/AUTONOMOUS_DEV.md` 플레이북의 우선순위를 따른다:
  실패 테스트/버그 > 테스트·엣지케이스 보강 > **데이터가 필요 없는** 골격·TODO 완성 > 문서 동기화.
- **② 구현** — 작고 명확한 변경 *하나*만. 큰 리팩터·아키텍처 변경은 이슈로 제안만.
- **③ 검증** — `pip install -e ".[mcp]" && python -m pytest -q` 전부 통과.
- **④ 자체 검수** — `/code-review` 를 **high 강도**로 돌려 변경분(diff)을 점검(정확성·재사용·
  단순화·효율·altitude). 사람이 한 번 더 보는 것과 같은 눈.
- **⑤ 수정** — 검수에서 나온 실제 문제를 고치고 다시 테스트 통과.
- **⑥ 통합** — `docs/decisions.md` 변경 이력에 한 줄, 명확한 메시지로 커밋, **PR만** 생성(병합 X).
- **⑦ 반복** — 다음 사이클로.

> "한 단계 = 작게, 검증·검수까지 닫힌 단위"가 핵심이다. 반쪽 구현·미검증 변경을 남기지 않는다.

---

## 2. 두 가지 실행 방식

### (A) 세션 내 연속 루프 — 사람이 지켜보며 빠르게
이미 켜진 Claude Code/Desktop 세션에서 한 사이클이 끝나면 **바로 다음 사이클**로 들어간다.
타이머가 필요 없다(세션 안에서 연속 실행). "계속/그만"으로 통제한다.
- 장점: 즉시 진행, 고강도 검수, 사람이 흐름을 본다.
- 한계: 세션이 떠 있는 동안만 진행(외부 스케줄러로 깨울 수 없는 환경에서는 무인 진행 불가).

### (B) 무인 자동 — GitHub Actions (이 저장소의 기본)
세션 없이도 **정기적으로** 같은 사이클을 호스트 모델이 수행한다.
- `.github/workflows/claude-autonomous.yml` — 정기(cron, 주 1회) + 수동(workflow_dispatch).
  플레이북을 읽고 작업 1개 골라 → 구현 → 테스트 → `/code-review` → 수정 → **PR 생성**.
- `.github/workflows/claude.yml` — 이슈/PR에 `@claude` 멘션 시 대화형으로 작업.
- 장점: 무인 반복, 사람은 PR만 검토/병합.
- 한계: GitHub Actions의 스케줄 트리거는 **기본 브랜치(main)** 에서만 동작.

> 두 방식은 **동일한 플레이북·동일한 가드레일**을 공유한다. (A)로 빠르게 돌리다가 (B)로 무인화하면 된다.

---

## 3. 가드레일 (반드시 지킴)

1. **보안 제0원칙** — 원천 PII(사업자번호·카드번호·거래처 실명)는 어떤 LLM 컨텍스트에도
   올리지 않는다. 테스트·예시는 **합성(가짜) 데이터만**. 실제 위하고 엑셀은 열지 않는다(.gitignore 차단).
2. **추측 금지** — 데이터가 있어야 확정되는 보류 항목(개인 상대계정/카면 의제/간이과세/
   봉사료·거래구분 실제값/계정과목 시트 실제 매핑)은 값을 박지 않는다. 골격/플래그/검토만.
3. **작은 단위** — 한 사이클 = 변경 하나. 검증·검수까지 닫는다.
4. **PR-only** — 자동 병합 없음. 사람이 검토 후 병합.
5. **중복 방지** — 무인 모드는 이미 열린 `claude/auto/*` PR이 있으면 새로 만들지 않고 종료.
   동시 실행 방지(`concurrency`).
6. **규칙 외부화** — 규칙·코드표는 코드에 하드코딩하지 않고 `config/*.yaml`.
7. **근거 기록** — 모든 변경은 `docs/decisions.md` 변경 이력에 남긴다.

---

## 4. 인증·비용

- **구독 OAuth 토큰(`CLAUDE_CODE_OAUTH_TOKEN`)** 사용 → 별도 API 토큰 과금 없이 구독 한도
  안에서 동작. `claude setup-token` 으로 1회 생성.
- 비용이 부담되면 cron 주기를 늘리거나 스케줄을 빼고 수동(dispatch)만 남긴다.

---

## 5. 무인 자동 활성화 체크리스트 (사람이 1회)

GitHub App 설치·토큰 생성·시크릿 등록·기본 브랜치 병합은 권한이 필요해 **사람이** 해야 한다.

1. **GitHub App 설치** — 로컬에서 `claude` 실행 후 `/install-github-app` (또는 https://github.com/apps/claude). 저장소 admin 필요.
2. **구독 토큰 생성** — 로컬에서 `claude setup-token`.
3. **시크릿 등록** — 저장소 Settings → Secrets and variables → Actions → `CLAUDE_CODE_OAUTH_TOKEN`.
4. **기본 브랜치(main)에 병합** — 워크플로가 main에 있어야 스케줄/이벤트 트리거가 동작.
5. (선택) **즉시 테스트** — Actions 탭 → "Claude 자율 개발" → Run workflow.

---

## 6. 통제 — 멈춤·검토·되돌리기

- **멈춤**: 무인 모드는 워크플로 비활성화 또는 cron 제거. 세션 모드는 "그만".
- **검토**: 모든 산출물은 PR로 온다. PR diff·테스트 결과·`docs/decisions.md` 기록으로 검토.
- **되돌리기**: PR을 닫거나 revert. 자동 병합이 없으므로 잘못된 변경이 곧장 main에 들어가지 않는다.

---

## 6.5. 코드 안의 루프 — Actor↔Evaluator 프레임워크 (`kafa/loop/`)

위 ①~⑦ 사이클이 *개발 프로세스*의 루프라면, `kafa/loop/` 는 **결과물 한 건을
생성→평가→재생성으로 끌어올리는** 루프를 코드로 제공한다(예: 근거 문장 품질 향상).
설계는 위 가드레일과 같은 원칙을 따른다.

```
critique = None
for i in range(max_iter):
    output  = Actor(input, critique)      # 생성(비평 있으면 반영)
    verdict = Evaluator(input, output)    # 채점 → {score, is_passed, critique}
    log(output, score, critique)          # 추적성(JSONL)
    if is_passed or score >= pass_score:  # 통과 → 종료
        return output
    critique = verdict.critique           # 비평 주입 후 재생성
return 최고점본 + 경고                       # 폴백(max_iter 도달)
```

- **프롬프트·루브릭 외부화** — `config/loops/<name>.yaml`(역할/작업/출력형식/루브릭/통과기준).
  코드 수정 없이 새 주제를 추가. `load_loop_spec("name")` 로 로드.
- **temperature 대신 effort** — 현재 Claude(Opus 4.8/Fable 5)는 temperature 미지원.
  Actor 는 `actor_effort`, Evaluator 는 결정성 위해 `evaluator_effort='low'` + 구조화 JSON.
- **공급자 비종속** — 모델 호출은 주입형 `Completion` 콜러블. 테스트는 가짜 콜러블,
  실사용은 `AnthropicCompletion`(opt-in — 추가 토큰). 자기평가는 evaluator 생략.
- **보안 제0원칙** — 프레임워크는 받은 문자열을 전달만 한다. `input_data` 의 비-PII 보장은
  호출자 책임이며, 예시 루브릭에도 "PII 미포함" 기준을 둔다.

```python
from kafa.loop import run_loop, load_loop_spec, AnthropicCompletion
spec = load_loop_spec("example")
res = run_loop(spec, "업태=음식점, 품명=커피, 유형=카과, 계정=(판)복리후생비",
               AnthropicCompletion(), log_path="logs/run.jsonl")
print(res.passed, res.best_score, res.best_output)
```

---

## 7. 다른 프로젝트로 재사용

이 방법론은 3개 부품으로 이식된다:
1. **플레이북**(`.github/AUTONOMOUS_DEV.md`) — 작업 우선순위·가드레일·금지·활성화.
2. **워크플로 2개**(`.github/workflows/claude.yml`, `claude-autonomous.yml`) — 대화형 + 무인.
3. **이 문서**(`docs/methodology.md`) — 방법론 설명.

새 프로젝트에서는 플레이북의 "우선순위/금지 항목"만 그 프로젝트에 맞게 바꾸면 된다.
