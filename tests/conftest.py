"""테스트 공통 — 개발자 PC 의 실제 운영 파일을 건드리지 않게 막는다.

- 수임처 속성(~/.kafa/clients.yaml) 대신 임시 파일
- kafa-run 기본 폴더(~/kafa-inbox, ~/kafa-out)와 설정(~/.kafa/run.json) 대신 임시 폴더
  (안 막으면 테스트가 실제 ~/kafa-out/_ai 의 질문지를 덮어쓰고 답지를 가져간다)
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_home_files(tmp_path, monkeypatch):
    from kafa import run_cli
    from kafa.config_loader import load_clients
    home = tmp_path / "_home"
    monkeypatch.setenv("KAFA_CLIENTS", str(home / ".kafa" / "clients.yaml"))
    monkeypatch.setattr(run_cli, "SETTINGS", home / ".kafa" / "run.json")
    monkeypatch.setattr(run_cli, "DEFAULT_INBOX", home / "kafa-inbox")
    monkeypatch.setattr(run_cli, "DEFAULT_OUT", home / "kafa-out")
    load_clients.cache_clear()
    yield
    load_clients.cache_clear()
