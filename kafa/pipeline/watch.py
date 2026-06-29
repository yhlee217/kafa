"""폴더 감시(폴링) — inbox 에 '안정된' 새 파일이 생기면 자동으로 run_pipeline 실행.

watchdog 등 외부 의존성 없이 폴링으로 동작(Windows 포함 어디서나·패키징 단순).
판단 규칙: 파일 크기가 직전 스캔과 같으면 '다운로드 완료'로 보고 처리한다(반쯤 받은
파일 처리 방지). 처리분은 run_pipeline 이 _archive 로 옮기므로 재처리되지 않는다.

테스트/제어용: stop(중단 신호), max_cycles(회차 제한), sleep(주입 가능).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from kafa.pipeline.notify import notify_pipeline
from kafa.pipeline.runner import PipelineResult, run_pipeline


def _scan(inbox: Path) -> dict[Path, int]:
    sizes: dict[Path, int] = {}
    for p in inbox.rglob("*.xlsx"):
        if p.name.startswith("~$"):
            continue
        try:
            sizes[p] = p.stat().st_size
        except OSError:
            pass
    return sizes


def watch(inbox, output_dir, *, interval: float = 10.0,
          client_type: Optional[str] = None, config_dir: Optional[str] = None,
          on_event: Optional[Callable[[PipelineResult], None]] = None,
          stop: Optional[Callable[[], bool]] = None,
          max_cycles: Optional[int] = None,
          sleep: Callable[[float], None] = time.sleep) -> list[PipelineResult]:
    """inbox 를 주기적으로 스캔해 안정된 새 파일이 있으면 처리. 실행 결과 목록 반환."""
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    if on_event is None:
        on_event = notify_pipeline

    prev: dict[Path, int] = {}
    runs: list[PipelineResult] = []
    cycles = 0
    while True:
        if stop and stop():
            break
        cur = _scan(inbox)
        if cur:
            influx = [p for p in cur if prev.get(p) != cur[p]]   # 새/변경 → 아직 받는 중일 수
            stable = [p for p in cur if prev.get(p) == cur[p]]   # 직전과 동일 → 받기 끝남
            if stable and not influx:
                res = run_pipeline(inbox, output_dir, client_type=client_type,
                                   config_dir=config_dir)
                runs.append(res)
                try:
                    on_event(res)
                except Exception:  # noqa: BLE001 — 알림 실패가 루프를 멈추지 않음
                    pass
                cur = _scan(inbox)   # 처리분은 _archive 로 빠짐 → 재스캔
        prev = cur
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        if stop and stop():
            break
        sleep(interval)
    return runs
