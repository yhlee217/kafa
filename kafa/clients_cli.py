"""kafa-clients — 수임처 속성 조사표 만들기/불러오기.

  kafa-clients template 수임처조사표.xlsx [--from-db out/kafa.db]
  kafa-clients import   수임처조사표.xlsx [--out config/clients.yaml]

담당자가 엑셀을 채워 오면 config/clients.yaml 로 변환한다. 배경: docs/domain_notes.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kafa-clients",
                                 description="수임처 속성 조사표(엑셀) ↔ clients.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="빈 조사표 만들기")
    t.add_argument("path", help="만들 파일 경로(.xlsx)")
    t.add_argument("--from-db", help="이미 처리한 수임처 이름을 미리 채움(out/kafa.db)")

    i = sub.add_parser("import", help="채워진 조사표 → clients.yaml")
    i.add_argument("path", help="채워진 조사표(.xlsx)")
    i.add_argument("--out", default="config/clients.yaml", help="저장 경로")

    args = ap.parse_args(argv)
    from kafa.clients import parse_template, to_yaml, write_template

    if args.cmd == "template":
        names: list[str] = []
        if args.from_db:
            db = Path(args.from_db)
            if db.exists():
                from kafa.store.db import VoucherStore
                with VoucherStore(db) as store:
                    names = store.clients()
            else:
                print(f"[안내] DB가 없어 빈 양식으로 만듭니다: {db}", file=sys.stderr)
        out = write_template(args.path, names)
        print(f"조사표 생성: {out.resolve()}" + (f"  (수임처 {len(names)}곳 미리 채움)"
                                              if names else ""))
        print("아버지께 전달해 '수임처' 시트를 채워 달라고 하세요.")
        return 0

    profiles = parse_template(args.path)
    if not profiles:
        print("조사표에서 읽은 수임처가 없습니다(이름 칸이 비어 있는지 확인).",
              file=sys.stderr)
        return 1
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(to_yaml(profiles), encoding="utf-8")
    filled = sum(1 for p in profiles.values() if p)
    print(f"수임처 {len(profiles)}곳 → {dest}  (속성 채워진 곳 {filled})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
