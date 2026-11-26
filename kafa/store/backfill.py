"""처리 이력에 '누가 정했나'(확정출처)를 되살린다 — 보관함(_archive)의 원본으로.

확정출처가 생기기 전(2026-11-26)에 쌓인 행은 위하고가 채워 둔 그대로(확정가능)인지 담당자가
확정한 것(전표확정)인지 DB 만으로는 알 수 없다. 원본 파일의 전표상태를 같은 키로 찾아 넣는다.
출력은 건수만(수임처·거래처 이름 없음).

    python -m kafa.store.backfill [--archive ~/kafa-out/_archive] [--db ~/kafa-out/kafa.db]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path


def backfill(archive: Path, db_path: Path) -> dict:
    from kafa.io_wehago.reader import read_download_xlsx
    from kafa.store.db import VoucherStore

    files = changed = failed = 0
    with VoucherStore(db_path) as db:
        for f in sorted(archive.rglob("*.xls*")):
            if f.name.startswith("~$"):
                continue
            rel = f.relative_to(archive)
            if len(rel.parts) < 2:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    rows = read_download_xlsx(f)
            except Exception:  # noqa: BLE001 — 형식이 다른 파일은 건너뛴다
                failed += 1
                continue
            files += 1
            changed += db.backfill_status(rel.parts[0], rows)
        counts = db.source_counts()
    return {"files": files, "changed": changed, "failed": failed, "sources": counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kafa.store.backfill",
                                 description="보관함 원본으로 처리 이력의 전표상태·확정출처를 채운다")
    ap.add_argument("--archive", default="~/kafa-out/_archive")
    ap.add_argument("--db", default="~/kafa-out/kafa.db")
    args = ap.parse_args(argv)
    archive = Path(args.archive).expanduser()
    db_path = Path(args.db).expanduser()
    if not archive.is_dir() or not db_path.exists():
        print(f"[중단] 보관함 또는 DB 가 없습니다: {archive} / {db_path}", file=sys.stderr)
        return 2
    r = backfill(archive, db_path)
    print(f"원본 {r['files']}개 읽음 · 전표상태를 채운 행 {r['changed']}건"
          + (f" · 읽지 못한 파일 {r['failed']}개" if r["failed"] else ""))
    print("계정이 있는 행의 확정출처:",
          " / ".join(f"{k or '(없음)'} {n}" for k, n in sorted(r["sources"].items(),
                                                              key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
