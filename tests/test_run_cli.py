"""kafa-run — 한 명령으로 수집→속성→처리. 각 단계는 가짜로 대체해 순서만 검증."""
import json

import pytest

from kafa import run_cli


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """세 단계를 가짜로 바꾸고 호출 순서를 기록한다."""
    calls = []
    monkeypatch.setattr(run_cli, "SETTINGS", tmp_path / "run.json")

    from kafa import clients_cli
    from kafa.fetch import cli as fetch_cli
    from kafa.pipeline import cli as pipeline_cli

    monkeypatch.setattr(fetch_cli, "main",
                        lambda argv: calls.append(("fetch", argv)) or 0)
    monkeypatch.setattr(clients_cli, "main",
                        lambda argv: calls.append(("clients", argv)) or 0)
    monkeypatch.setattr(pipeline_cli, "main",
                        lambda argv: calls.append(("pipeline", argv)) or 0)
    master = tmp_path / "master.xlsx"
    master.write_text("x", encoding="utf-8")
    return calls, master, tmp_path


def test_runs_three_steps_in_order(wired):
    calls, master, tmp = wired
    rc = run_cli.main(["--master", str(master), "--inbox", str(tmp / "in"),
                       "--out", str(tmp / "out")])
    assert rc == 0
    assert [c[0] for c in calls] == ["fetch", "clients", "pipeline"]
    assert "--whole" in calls[0][1]
    assert calls[1][1][0] == "from-master"
    assert calls[2][1] == [str(tmp / "in"), str(tmp / "out")]


def test_remembers_paths_for_next_run(wired):
    calls, master, tmp = wired
    run_cli.main(["--master", str(master), "--inbox", str(tmp / "in"),
                  "--out", str(tmp / "out")])
    saved = json.loads((tmp / "run.json").read_text(encoding="utf-8"))
    assert saved["master"] == str(master)

    calls.clear()
    assert run_cli.main([]) == 0          # 인자 없이도 돈다
    assert [c[0] for c in calls] == ["fetch", "clients", "pipeline"]


def test_first_run_requires_master(tmp_path, monkeypatch):
    monkeypatch.setattr(run_cli, "SETTINGS", tmp_path / "run.json")
    with pytest.raises(SystemExit) as e:
        run_cli.main([])
    assert e.value.code != 0


def test_missing_master_file_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(run_cli, "SETTINGS", tmp_path / "run.json")
    with pytest.raises(SystemExit) as e:
        run_cli.main(["--master", str(tmp_path / "없음.xlsx")])
    assert e.value.code != 0


def test_skip_fetch_processes_only(wired):
    calls, master, tmp = wired
    run_cli.main(["--master", str(master), "--skip-fetch"])
    assert [c[0] for c in calls] == ["clients", "pipeline"]


def test_dry_run_stops_after_fetch(wired):
    calls, master, tmp = wired
    run_cli.main(["--master", str(master), "--dry-run"])
    assert [c[0] for c in calls] == ["fetch"]
    assert "--dry-run" in calls[0][1]


def test_partial_failure_still_processes(wired, monkeypatch):
    """일부 수임처가 실패해도(코드 1) 받은 것은 처리한다."""
    calls, master, tmp = wired
    from kafa.fetch import cli as fetch_cli
    monkeypatch.setattr(fetch_cli, "main",
                        lambda argv: calls.append(("fetch", argv)) or 1)
    run_cli.main(["--master", str(master)])
    assert [c[0] for c in calls] == ["fetch", "clients", "pipeline"]


def test_calibration_error_stops_everything(wired, monkeypatch):
    """보정 안 됨(코드 2)이면 뒤 단계를 돌리지 않는다."""
    calls, master, tmp = wired
    from kafa.fetch import cli as fetch_cli
    monkeypatch.setattr(fetch_cli, "main",
                        lambda argv: calls.append(("fetch", argv)) or 2)
    assert run_cli.main(["--master", str(master)]) == 2
    assert [c[0] for c in calls] == ["fetch"]
