"""처리 완료 알림 — Windows 토스트(가능 시), 아니면 콘솔 출력.

win10toast 가 있으면 토스트, 없으면(비-Windows/미설치) 콘솔로 폴백한다.
알림 실패는 절대 파이프라인을 멈추지 않는다(호출측에서 감싼다).
"""
from __future__ import annotations

from kafa.pipeline.runner import PipelineResult


def notify(title: str, message: str) -> None:
    try:
        from win10toast import ToastNotifier  # type: ignore
        ToastNotifier().show_toast(title, message, duration=5, threaded=True)
        return
    except Exception:  # noqa: BLE001 — 미설치/비윈도우 → 콘솔 폴백
        pass
    print(f"[알림] {title} — {message}")


def notify_pipeline(res: PipelineResult) -> None:
    """파이프라인 1회 실행 결과를 사람이 읽는 한 줄 알림으로(비-PII 집계만)."""
    n = len(res.outcomes)
    written = sum(o.written for o in res.outcomes)
    msg = f"{n}개 파일 처리 · {written}건 작성 · DB 누적 {res.total_in_db}건"
    if res.failures:
        msg += f" · 실패 {len(res.failures)}건"
    notify("kafa 처리 완료", msg)
