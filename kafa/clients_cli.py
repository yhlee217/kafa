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

    t = sub.add_parser("template", help="조사표 만들기(이름 미리 채우기 가능)")
    t.add_argument("path", help="만들 파일 경로(.xlsx)")
    t.add_argument("--from-excel",
                   help="거래처 목록 엑셀에서 이름 추출(위하고 거래처관리 › 내보내기 등)")
    t.add_argument("--from-inbox", help="inbox 하위 폴더명에서 추출")
    t.add_argument("--from-list", help="한 줄에 하나씩 적은 텍스트 파일")
    t.add_argument("--from-db", help="이미 처리한 수임처(out/kafa.db)")
    t.add_argument("--force", action="store_true",
                   help="같은 이름의 파일이 있어도 덮어쓴다(담당자가 채운 내용이 지워질 수 있음)")

    m = sub.add_parser("from-master",
                       help="수임처 마스터 엑셀의 '구분'(개인/법인)으로 clients.yaml 채우기")
    m.add_argument("path", help="수임처 마스터 엑셀(.xlsx)")
    m.add_argument("--out", default="config/clients.yaml", help="저장 경로")
    m.add_argument("--replace", action="store_true",
                   help="기존 내용을 지우고 새로 쓴다(기본은 사람이 적은 값 보존)")

    i = sub.add_parser("import", help="채워진 조사표 → clients.yaml")
    i.add_argument("path", help="채워진 조사표(.xlsx)")
    i.add_argument("--out", default="config/clients.yaml", help="저장 경로")

    args = ap.parse_args(argv)
    from kafa.clients import parse_template, to_yaml, write_template

    if args.cmd == "from-master":
        from kafa.clients import (load_yaml_profiles, merge_profiles,
                                  profiles_from_master)
        src = Path(args.path)
        if not src.exists():
            print(f"[중단] 파일이 없습니다: {src}", file=sys.stderr)
            return 2
        new_profiles = profiles_from_master(src)
        if not new_profiles:
            print("[중단] 수임처를 읽지 못했습니다(회사명 칸을 찾을 수 없음).",
                  file=sys.stderr)
            return 2
        defaults, existing = load_yaml_profiles(args.out)
        merged = (new_profiles if args.replace
                  else merge_profiles(existing, new_profiles))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(to_yaml(merged, defaults=defaults or None),
                       encoding="utf-8")

        def _count(kind):
            return sum(1 for v in merged.values() if v.get("client_type") == kind)

        unknown = sum(1 for v in merged.values() if not v.get("client_type"))
        known_emp = sum(1 for v in merged.values() if "has_employees" in v)
        print(f"clients.yaml 갱신: {out.resolve()}  — 수임처 {len(merged)}곳")
        print(f"  개인 {_count('individual')} / 법인 {_count('corporate')}"
              + (f" / 구분없음 {unknown}" if unknown else ""))
        print(f"  직원 유무 확인됨 {known_emp}곳"
              f" — 나머지는 기본값(직원 있음)으로 봅니다.")
        if known_emp < len(merged):
            print("  직원 유무는 자료로 알 수 없습니다. 조사표로 받으세요:")
            print(f"    kafa-clients template 조사표.xlsx --from-excel \"{src}\"")
        return 0

    if args.cmd == "template":
        from kafa.clients import (names_from_inbox, names_from_text,
                                  profiles_from_excel)
        # 이미 있는 조사표는 담당자가 채운 것일 수 있다 — 아무 말 없이 덮어쓰지 않는다.
        target = Path(args.path).expanduser()
        if target.exists() and not args.force:
            print(f"[중단] 이미 있는 파일입니다: {target}\n"
                  "  담당자가 채운 내용이 지워질 수 있어 덮어쓰지 않습니다.\n"
                  "  다른 이름으로 만들거나, 정말 새로 만들려면 --force 를 붙이세요.",
                  file=sys.stderr)
            return 1
        # 이름을 가져오라고 한 곳이 없으면 멈춘다 — 빈 양식으로 넘어가면 실수를 알아채기 어렵다.
        for flag, value in (("--from-excel", args.from_excel), ("--from-inbox", args.from_inbox),
                            ("--from-list", args.from_list), ("--from-db", args.from_db)):
            if value and not Path(value).expanduser().exists():
                print(f"[중단] {flag} 경로가 없습니다: {value}\n"
                      "  경로를 확인해 주세요(명령이 중간에 잘리지 않았는지도).",
                      file=sys.stderr)
                return 2
        names: list = []
        seen: set[str] = set()

        def add(items, src):
            new = []
            for it in items:
                nm = it["name"] if isinstance(it, dict) else it
                if nm in seen:
                    continue
                seen.add(nm)
                new.append(it)
            names.extend(new)
            if items:
                print(f"  {src}: {len(items)}곳 (신규 {len(new)})")

        if args.from_excel:
            recs = profiles_from_excel(args.from_excel)
            add(recs, "엑셀")
            known = sum(1 for r in recs if r.get("client_type"))
            if known:
                print(f"    └ 개인/법인까지 확인됨: {known}곳 (담당자는 직원 유무만 고르면 됨)")
        if args.from_inbox:
            add(names_from_inbox(args.from_inbox), "inbox 폴더")
        if args.from_list:
            add(names_from_text(args.from_list), "목록 파일")
        if args.from_db:
            from kafa.store.db import VoucherStore
            with VoucherStore(Path(args.from_db).expanduser()) as store:
                add(store.clients(), "DB")

        out = write_template(target, names)
        print(f"조사표 생성: {out.resolve()}" + (f"  — 수임처 {len(names)}곳 미리 채움"
                                              if names else " (빈 양식)"))
        if not names:
            print("  이름을 미리 채우려면 --from-excel / --from-inbox / --from-list 사용",
                  file=sys.stderr)
        print("담당자에게 전달해 '수임처' 시트의 나머지 칸을 채워 달라고 하세요.")
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
