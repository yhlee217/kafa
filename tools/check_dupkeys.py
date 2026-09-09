"""읽기 전용 점검 — 키 충돌로 사라졌던 전표가 몇 건인지 센다.

아무것도 바꾸지 않는다. DB 도 dup.json 도 건드리지 않고 원본 엑셀만 읽는다.
사용:  python check_dupkeys.py ~/kafa-out/_archive
"""
import sys
import warnings

# 위하고 다운로드본은 기본 스타일이 없어 openpyxl 이 파일마다 경고를 낸다 — 출력만 가린다.
warnings.filterwarnings("ignore", message="Workbook contains no default style",
                        module="openpyxl")
from collections import Counter, defaultdict
from pathlib import Path

from kafa.io_wehago.reader import read_download_xlsx

root = Path(sys.argv[1] if len(sys.argv) > 1 else "~/kafa-out/_archive").expanduser()
files = sorted(p for p in root.rglob("*.xlsx") if not p.name.startswith("~$"))
if not files:
    sys.exit(f"엑셀을 못 찾았습니다: {root}")

총행 = 잃은행 = 0
잃은금액 = 0
per_client = defaultdict(int)
for f in files:
    try:
        rows = read_download_xlsx(f)
    except Exception as e:                      # noqa: BLE001
        print(f"  [건너뜀] {f.name}: {type(e).__name__}")
        continue
    seen = Counter()
    for r in rows:
        총행 += 1
        key = (f"{r.연도}-{r.일자}", r.거래처, r.사업자등록번호, str(r.합계), r.품명)
        if seen[key]:                            # 앞에 같은 행이 이미 있었다
            잃은행 += 1
            per_client[f.parent.name] += 1
            try:
                잃은금액 += int(r.합계)
            except (TypeError, ValueError):
                pass
        seen[key] += 1

print(f"파일 {len(files)}개 / 전표 {총행:,}행")
print(f"키가 겹쳐 예전에 사라졌던 전표: {잃은행}건 / {잃은금액:,}원")
if per_client:
    print("\n수임처별:")
    for name, n in sorted(per_client.items(), key=lambda kv: -kv[1]):
        print(f"  {name}: {n}건")
else:
    print("→ 겹치는 행이 없습니다. 이번 자료에서는 잃은 전표가 없었습니다.")
