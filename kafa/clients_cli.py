"""kafa-clients — 수임처 속성 조사표 만들기/불러오기.

  kafa-clients template 수임처조사표.xlsx [--from-db out/kafa.db]
  kafa-clients import   수임처조사표.xlsx(.xls 도 됨) [--out ~/.kafa/clients.yaml]

담당자가 엑셀을 채워 오면 수임처 속성 파일로 합친다. 기본 저장 위치는 레포 **밖**
~/.kafa/clients.yaml(수임처 실명이 들어가므로 git 이 추적하는 config/ 에 두지 않는다).
처음 저장할 때는 예전에 config/clients.yaml 에 적힌 값을 가져와 이어 쓴다.
배경: docs/domain_notes.md
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
    m.add_argument("--out", default=None, help="저장 경로(기본 ~/.kafa/clients.yaml)")
    m.add_argument("--replace", action="store_true",
                   help="기존 내용을 지우고 새로 쓴다(기본은 사람이 적은 값 보존)")

    i = sub.add_parser("import", help="채워진 조사표 → clients.yaml")
    i.add_argument("path", help="채워진 조사표(.xlsx 또는 엑셀에서 다시 저장한 .xls)")
    i.add_argument("--out", default=None, help="저장 경로(기본 ~/.kafa/clients.yaml)")
    i.add_argument("--db", default=None,
                   help="처리 이력 DB — 조사표 이름이 처리한 수임처와 맞는지 센다"
                        "(기본: kafa-run 결과 폴더의 kafa.db)")

    ml = sub.add_parser("meal", help="수임처의 식대 계정 지정(예: 접대비) — 추천된 복리후생비 식대를 그 계정으로")
    ml.add_argument("account", nargs="?", default="",
                    help="계정 이름 또는 코드(예: 접대비, 813). --clear 면 생략")
    who = ml.add_mutually_exclusive_group(required=True)
    who.add_argument("--client", help="수임처 이름")
    who.add_argument("--where-note", help="조사표 메모에 이 문구가 있는 수임처(이름을 치지 않아도 됨)")
    ml.add_argument("--clear", action="store_true", help="지정을 푼다")
    ml.add_argument("--out", default=None, help="저장 경로(기본 ~/.kafa/clients.yaml)")

    args = ap.parse_args(argv)
    from kafa.clients import merge_profiles, parse_template, write_template

    if args.cmd == "meal":
        return _cmd_meal(args)

    if args.cmd == "from-master":
        from kafa.clients import profiles_from_master
        src = Path(args.path)
        if not src.exists():
            print(f"[중단] 파일이 없습니다: {src}", file=sys.stderr)
            return 2
        new_profiles = profiles_from_master(src)
        if not new_profiles:
            print("[중단] 수임처를 읽지 못했습니다(회사명 칸을 찾을 수 없음).",
                  file=sys.stderr)
            return 2
        out = _out_path(args.out)
        defaults, existing = _existing(out)
        merged = (new_profiles if args.replace
                  else merge_profiles(existing, new_profiles))
        _save(out, merged, defaults)

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
        from kafa.clients import names_from_inbox, names_from_text, profiles_from_excel
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

    src = Path(args.path).expanduser()
    if not src.exists():
        print(f"[중단] 파일이 없습니다: {src}", file=sys.stderr)
        return 2
    issues: list = []
    profiles = parse_template(src, issues=issues)
    if not profiles:
        print("조사표에서 읽은 수임처가 없습니다(이름 칸이 비어 있는지 확인).",
              file=sys.stderr)
        return 1
    dest = _out_path(args.out)
    defaults, existing = _existing(dest)
    _save(dest, merge_profiles(existing, profiles), defaults)

    # 출력에는 수임처 이름을 쓰지 않는다(대화에 붙여넣어도 되게) — 건수만.
    def _n(key, val):
        return sum(1 for p in profiles.values() if p.get(key) == val)

    print(f"조사표 수임처 {len(profiles)}곳 → {dest}")
    print(f"  개인 {_n('client_type', 'individual')} / 법인 {_n('client_type', 'corporate')}"
          f" / 빈칸 {sum(1 for p in profiles.values() if 'client_type' not in p)}")
    print(f"  직원 있음 {_n('has_employees', True)} / 없음 {_n('has_employees', False)}"
          f" / 빈칸 {sum(1 for p in profiles.values() if 'has_employees' not in p)}")
    print(f"  메모 {sum(1 for p in profiles.values() if p.get('note'))}곳")
    if issues:
        print(f"  [확인] 알아듣지 못한 표기 {len(issues)}건 — 그 칸은 비운 것으로 봤습니다:")
        for row, col, val in issues[:10]:
            print(f"    {row}행 {col}: '{val}'")
    db = _db_path(args.db)
    if db is not None:
        from kafa.store.db import VoucherStore
        store = VoucherStore(db)
        try:
            done = set(store.clients())
        finally:
            store.close()
        hit = sum(1 for cid in profiles if cid in done)
        if done:
            print(f"  처리 이력의 수임처와 이름이 맞는 곳: {hit}/{len(profiles)}"
                  f" (처리 이력 {len(done)}곳)")
        if done and hit < len(profiles):
            print("    맞지 않는 곳은 폐업 등으로 자료가 없거나, 조사표 이름이 폴더 이름과 다른 곳입니다.")
        if done:
            _report_uncovered(done)
    return 0


def _report_uncovered(done: set) -> None:
    """처리 이력에 있는데 직원 유무가 없는 수임처 — 건수만. '(주)' 표기만 다르면 이어 붙인다."""
    from kafa.config_loader import client_profile, load_clients
    load_clients.cache_clear()
    clients = load_clients()["clients"]
    linked = missing = 0
    for cid in done:
        if "has_employees" in (clients.get(cid) or {}):
            continue
        prof = client_profile(cid)
        if prof.get("matched_by") == "name_variant" and "has_employees" in prof:
            linked += 1
        else:
            missing += 1
    if linked:
        print(f"  '(주)' 같은 표기만 다른 이름의 값을 이어 쓴 곳: {linked}곳")
    if missing:
        print(f"  [확인] 처리 이력에 있는데 직원 유무가 없는 곳: {missing}곳 — 기본값(직원 있음)으로 처리됩니다")


def _cmd_meal(args) -> int:
    """수임처 한 곳의 식대 계정을 지정/해제. 출력에 수임처 이름은 가려서 쓴다."""
    from kafa.clients import resolve_account_text
    from kafa.config_loader import same_client_by_name
    from kafa.fetch.plan import safe_name
    from kafa.security import mask_name

    out = _out_path(args.out)
    defaults, clients = _existing(out)
    if args.where_note:
        targets = [k for k, v in clients.items() if args.where_note in ((v or {}).get("note") or "")]
    else:
        cid = safe_name(args.client)
        alias = cid if cid in clients else same_client_by_name(cid, clients)
        targets = [alias] if alias else []
    if len(targets) != 1:
        print(f"[중단] 맞는 수임처가 {len(targets)}곳입니다 — 한 곳만 맞도록 다시 주세요"
              + ("(메모 문구를 더 길게)" if args.where_note else ""), file=sys.stderr)
        return 2
    cid = targets[0]
    prof = clients.setdefault(cid, {}) or {}
    clients[cid] = prof
    if args.clear:
        prof.pop("meal_account", None)
        msg = "식대 계정 지정을 풀었습니다"
    else:
        code, name = resolve_account_text(args.account)
        if code is None:
            print(f"[중단] {name}", file=sys.stderr)
            return 2
        prof["meal_account"] = code
        msg = f"식대 계정 → {name} [{code}]"
    _save(out, clients, defaults)
    print(f"{mask_name(cid, keep=1)}: {msg}")
    print("  프로그램이 복리후생비로 추천한 식대(음식점 업종)만 바뀝니다."
          " 위하고·담당자가 이미 채운 행은 그대로입니다.")
    return 0


def _out_path(arg) -> Path:
    from kafa.config_loader import local_clients_path
    return Path(arg).expanduser() if arg else local_clients_path()


def _existing(out: Path) -> tuple[dict, dict]:
    """저장할 파일의 기존 값. 아직 없으면 예전 위치(레포 config/clients.yaml)에서 이어받는다."""
    from kafa.clients import load_yaml_profiles
    from kafa.config_loader import _DEFAULT_CONFIG_DIR
    if out.exists():
        return load_yaml_profiles(out)
    return load_yaml_profiles(_DEFAULT_CONFIG_DIR / "clients.yaml")


def _save(out: Path, profiles: dict, defaults: dict) -> None:
    from kafa.clients import to_yaml
    from kafa.config_loader import load_clients
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_yaml(profiles, defaults=defaults or None), encoding="utf-8")
    load_clients.cache_clear()


def _db_path(arg):
    if arg:
        p = Path(arg).expanduser()
        return p if p.exists() else None
    from kafa.run_cli import DEFAULT_OUT, load_settings
    p = Path(load_settings().get("out") or DEFAULT_OUT).expanduser() / "kafa.db"
    return p if p.exists() else None


if __name__ == "__main__":
    raise SystemExit(main())
