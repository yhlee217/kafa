"""브라우저 세션 — **사람이 로그인한 상태를 이어받는다**.

두 가지 방식:
  1) persistent (기본): 스크립트가 브라우저를 열고, 사람이 그 창에서 직접 로그인한다.
     로그인 상태는 user_data_dir 에 남아 다음 실행 때 재사용된다(재로그인 최소화).
  2) attach: 사람이 이미 띄워둔 크롬(원격 디버깅 포트)에 붙는다.
     실행 예) chrome.exe --remote-debugging-port=9222

**코드는 아이디/비밀번호/인증서/OTP 를 절대 다루지 않는다.** 로그인은 전적으로 사람이 하고,
스크립트는 로그인 완료를 기다렸다가 화면 조작만 이어서 한다.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

# 로그인·세션 정보가 남는 프로필 디렉터리(로컬 전용). 기본값은 사용자 홈 아래.
DEFAULT_PROFILE = Path.home() / ".kafa" / "browser-profile"


@contextmanager
def browser_page(*, profile_dir=None, attach_port: int | None = None,
                 headless: bool = False, downloads_dir=None):
    """Playwright 페이지를 열어 넘긴다(컨텍스트 매니저).

    headless 는 기본 False — 사람이 로그인하고 진행을 지켜봐야 하므로 창을 띄운다.
    """
    from playwright.sync_api import sync_playwright

    dl = str(downloads_dir) if downloads_dir else None
    with sync_playwright() as pw:
        if attach_port:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{attach_port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context(
                accept_downloads=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                yield page
            finally:
                browser.close()          # 붙은 브라우저 자체는 사람이 계속 씀
        else:
            profile = Path(profile_dir or DEFAULT_PROFILE)
            profile.mkdir(parents=True, exist_ok=True)
            ctx = pw.chromium.launch_persistent_context(
                str(profile), headless=headless, accept_downloads=True,
                downloads_path=dl)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                yield page
            finally:
                ctx.close()


def wait_for_human(message: str, *, input_fn=input) -> None:
    """사람이 로그인/화면 이동을 끝낼 때까지 기다린다(엔터로 진행).

    자동 로그인을 하지 않는다는 설계의 핵심 지점. 로그인·인증서·OTP 는 사람 몫.
    """
    print("\n" + "=" * 60)
    print(message)
    print("=" * 60)
    input_fn("준비되면 엔터를 누르세요... ")
